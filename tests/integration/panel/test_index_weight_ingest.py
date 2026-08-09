"""`index_weight` end to end: Tushare -> month batches -> partitions -> composition + weights.

The whole chain runs against a real `PanelStore` on a real DuckDB catalog with real Parquet
files. Only the HTTP transport is doubled, and what it serves is real market data captured live
on 2026-08-09: `000300.SH`'s publications for 2009-11-30, 2009-12-31 and 2010-01-29, in full.

Those three months were chosen because everything this issue has to be honest about happens
inside them.

**The 298.** The 2009-12-31 publication carries **298** constituents, not 300. `600001.SH`
邯郸钢铁 and `600357.SH` 承德钒钛 were both terminated on 2009-12-29 and the month-end
publication dropped them with no replacement; the 2010-01-29 review restored the count. It is
the only off-nominal publication in the 633 measured, and it is why nothing checks a constituent
count against the index's name.

**The staleness.** A question about 2009-12-15 is answered from the 2009-11-30 publication, and
`test_a_mid_month_question_says_which_publication_it_came_from` reads that out of the store: the
weights are 15 days old and the answer says so.

**The stale composition.** `600001.SH` is in the 2009-11-30 publication and was terminated
2009-12-29, so a forward-filled 2009-12-30 membership names a security the registry says had
already gone. `test_a_forward_filled_membership_can_disagree_with_the_registry` reproduces that
against a real `StockUniverse`, and the report is what surfaces it.

**The overwrite.** A per-index backfill loop would leave a year holding whichever index it wrote
last, because the partition key has no index dimension. The section at the bottom shows that
write being refused.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.domain.index_membership import (
    CSI300_INDEX_CODE,
    CSI500_INDEX_CODE,
    INDEX_WEIGHT_DATASET,
    IndexMembershipError,
    IndexMembershipHorizonError,
    constituent_listing_report,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelBatchError
from openalpha_cn.domain.stock_universe import SecurityLifecycle, build_stock_universe
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_index_membership, write_index_weights
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TUSHARE_RESPONSE_TRUNCATION_FLAG, TushareProvider

RESPONSE_FIELDS = ["index_code", "con_code", "trade_date", "weight"]


# 300 constituents summing to 99.997, published 2009-11-30.
CSI300_20091130 = (
    "000001.SZ=1.372 000002.SZ=2.033 000009.SZ=0.201 000012.SZ=0.144 000021.SZ=0.108 "
    "000024.SZ=0.39 000027.SZ=0.161 000031.SZ=0.215 000039.SZ=0.186 000046.SZ=0.134 "
    "000059.SZ=0.128 000060.SZ=0.355 000061.SZ=0.104 000063.SZ=0.661 000069.SZ=0.595 "
    "000089.SZ=0.092 000100.SZ=0.208 000157.SZ=0.318 000301.SZ=0.105 000338.SZ=0.344 "
    "000401.SZ=0.159 000402.SZ=0.497 000422.SZ=0.2 000423.SZ=0.226 000425.SZ=0.222 "
    "000488.SZ=0.133 000527.SZ=0.501 000528.SZ=0.138 000538.SZ=0.205 000543.SZ=0.055 "
    "000559.SZ=0.057 000562.SZ=0.251 000568.SZ=0.499 000612.SZ=0.118 000623.SZ=0.501 "
    "000625.SZ=0.176 000629.SZ=0.418 000630.SZ=0.265 000651.SZ=0.655 000652.SZ=0.165 "
    "000667.SZ=0.165 000680.SZ=0.134 000685.SZ=0.093 000686.SZ=0.184 000690.SZ=0.122 "
    "000709.SZ=0.236 000717.SZ=0.142 000718.SZ=0.161 000725.SZ=0.133 000728.SZ=0.286 "
    "000729.SZ=0.154 000758.SZ=0.115 000768.SZ=0.295 000778.SZ=0.231 000783.SZ=0.433 "
    "000792.SZ=0.494 000793.SZ=0.102 000800.SZ=0.355 000807.SZ=0.142 000822.SZ=0.074 "
    "000825.SZ=0.496 000839.SZ=0.257 000858.SZ=0.983 000876.SZ=0.094 000878.SZ=0.36 "
    "000895.SZ=0.27 000897.SZ=0.151 000898.SZ=0.483 000900.SZ=0.127 000912.SZ=0.058 "
    "000917.SZ=0.104 000927.SZ=0.075 000932.SZ=0.144 000933.SZ=0.289 000937.SZ=0.31 "
    "000951.SZ=0.086 000959.SZ=0.122 000960.SZ=0.172 000968.SZ=0.129 000969.SZ=0.13 "
    "000983.SZ=0.904 000999.SZ=0.147 002001.SZ=0.121 002024.SZ=0.931 002028.SZ=0.128 "
    "002038.SZ=0.13 002122.SZ=0.098 002128.SZ=0.112 002142.SZ=0.316 002155.SZ=0.135 "
    "002194.SZ=0.056 002202.SZ=0.451 002242.SZ=0.083 002244.SZ=0.08 002269.SZ=0.088 "
    "600000.SH=2.125 600001.SH=0.197 600004.SH=0.083 600005.SH=0.464 600006.SH=0.096 "
    "600008.SH=0.118 600009.SH=0.268 600010.SH=0.223 600011.SH=0.397 600015.SH=0.411 "
    "600016.SH=2.64 600017.SH=0.055 600018.SH=0.443 600019.SH=0.782 600022.SH=0.12 600026.SH=0.16 "
    "600027.SH=0.092 600028.SH=0.923 600029.SH=0.181 600030.SH=2.468 600031.SH=0.393 "
    "600033.SH=0.074 600036.SH=3.452 600037.SH=0.162 600048.SH=0.639 600050.SH=1.009 "
    "600058.SH=0.152 600066.SH=0.123 600068.SH=0.284 600085.SH=0.099 600087.SH=0.125 "
    "600089.SH=0.651 600096.SH=0.14 600100.SH=0.197 600102.SH=0.066 600104.SH=0.605 "
    "600108.SH=0.149 600109.SH=0.127 600110.SH=0.11 600111.SH=0.258 600117.SH=0.09 600118.SH=0.07 "
    "600123.SH=0.272 600125.SH=0.142 600132.SH=0.15 600143.SH=0.132 600150.SH=0.191 "
    "600151.SH=0.072 600153.SH=0.201 600158.SH=0.102 600169.SH=0.112 600170.SH=0.102 "
    "600176.SH=0.068 600177.SH=0.29 600183.SH=0.102 600188.SH=0.269 600196.SH=0.266 "
    "600208.SH=0.21 600210.SH=0.145 600216.SH=0.164 600219.SH=0.229 600220.SH=0.143 "
    "600221.SH=0.073 600236.SH=0.086 600251.SH=0.093 600256.SH=0.202 600266.SH=0.138 "
    "600269.SH=0.173 600270.SH=0.058 600271.SH=0.124 600276.SH=0.278 600282.SH=0.073 "
    "600299.SH=0.069 600307.SH=0.12 600308.SH=0.113 600309.SH=0.354 600316.SH=0.125 "
    "600320.SH=0.333 600325.SH=0.262 600331.SH=0.215 600348.SH=0.417 600350.SH=0.066 "
    "600352.SH=0.17 600357.SH=0.085 600362.SH=0.379 600376.SH=0.098 600380.SH=0.078 "
    "600383.SH=0.56 600395.SH=0.182 600415.SH=0.29 600418.SH=0.151 600426.SH=0.148 "
    "600428.SH=0.132 600432.SH=0.215 600456.SH=0.091 600489.SH=0.431 600497.SH=0.226 "
    "600500.SH=0.152 600508.SH=0.132 600516.SH=0.1 600518.SH=0.231 600519.SH=1.196 "
    "600528.SH=0.173 600547.SH=0.533 600548.SH=0.033 600549.SH=0.077 600550.SH=0.352 "
    "600569.SH=0.099 600582.SH=0.147 600583.SH=0.276 600585.SH=0.425 600588.SH=0.105 "
    "600595.SH=0.131 600596.SH=0.185 600597.SH=0.05 600598.SH=0.183 600600.SH=0.228 "
    "600601.SH=0.165 600611.SH=0.138 600616.SH=0.08 600631.SH=0.21 600635.SH=0.242 "
    "600638.SH=0.122 600639.SH=0.062 600642.SH=0.303 600643.SH=0.189 600649.SH=0.289 "
    "600653.SH=0.148 600655.SH=0.317 600660.SH=0.256 600663.SH=0.2 600664.SH=0.255 "
    "600674.SH=0.123 600675.SH=0.226 600685.SH=0.084 600688.SH=0.196 600690.SH=0.344 "
    "600694.SH=0.216 600717.SH=0.181 600718.SH=0.119 600737.SH=0.148 600739.SH=0.605 "
    "600741.SH=0.238 600748.SH=0.132 600770.SH=0.08 600779.SH=0.129 600782.SH=0.065 "
    "600795.SH=0.358 600804.SH=0.114 600808.SH=0.213 600809.SH=0.1 600811.SH=0.175 "
    "600812.SH=0.163 600816.SH=0.113 600820.SH=0.131 600832.SH=0.321 600835.SH=0.104 "
    "600837.SH=1.203 600839.SH=0.177 600859.SH=0.166 600874.SH=0.047 600875.SH=0.241 "
    "600879.SH=0.148 600881.SH=0.315 600886.SH=0.117 600895.SH=0.198 600900.SH=1.079 "
    "600970.SH=0.113 600997.SH=0.286 601001.SH=0.284 601005.SH=0.039 601006.SH=0.773 "
    "601009.SH=0.41 601088.SH=2.13 601111.SH=0.245 601166.SH=2.449 601168.SH=0.474 "
    "601169.SH=1.406 601186.SH=0.518 601318.SH=2.972 601328.SH=2.8 601333.SH=0.25 601390.SH=0.593 "
    "601398.SH=1.398 601588.SH=0.183 601600.SH=0.508 601601.SH=1.378 601628.SH=0.836 "
    "601666.SH=0.41 601699.SH=0.461 601727.SH=0.116 601766.SH=0.419 601808.SH=0.17 "
    "601857.SH=0.922 601866.SH=0.213 601872.SH=0.14 601898.SH=0.465 601899.SH=0.384 "
    "601918.SH=0.128 601919.SH=0.598 601939.SH=0.972 601958.SH=0.376 601988.SH=0.487 "
    "601991.SH=0.109 601998.SH=0.272"
)

# 298 constituents summing to 99.993, published 2009-12-31.
CSI300_20091231 = (
    "000001.SZ=1.355 000002.SZ=1.873 000009.SZ=0.171 000012.SZ=0.136 000021.SZ=0.101 "
    "000024.SZ=0.328 000027.SZ=0.16 000031.SZ=0.182 000039.SZ=0.202 000046.SZ=0.116 "
    "000059.SZ=0.125 000060.SZ=0.358 000061.SZ=0.115 000063.SZ=0.742 000069.SZ=0.478 "
    "000089.SZ=0.091 000100.SZ=0.216 000157.SZ=0.312 000301.SZ=0.107 000338.SZ=0.364 "
    "000401.SZ=0.168 000402.SZ=0.431 000422.SZ=0.207 000423.SZ=0.245 000425.SZ=0.218 "
    "000488.SZ=0.136 000527.SZ=0.518 000528.SZ=0.152 000538.SZ=0.231 000543.SZ=0.052 "
    "000559.SZ=0.057 000562.SZ=0.249 000568.SZ=0.487 000612.SZ=0.144 000623.SZ=0.505 "
    "000625.SZ=0.174 000629.SZ=0.394 000630.SZ=0.257 000651.SZ=0.681 000652.SZ=0.153 "
    "000667.SZ=0.138 000680.SZ=0.138 000685.SZ=0.098 000686.SZ=0.176 000690.SZ=0.119 "
    "000709.SZ=0.23 000717.SZ=0.14 000718.SZ=0.125 000725.SZ=0.133 000728.SZ=0.3 000729.SZ=0.163 "
    "000758.SZ=0.106 000768.SZ=0.339 000778.SZ=0.23 000783.SZ=0.45 000792.SZ=0.47 000793.SZ=0.103 "
    "000800.SZ=0.379 000807.SZ=0.144 000822.SZ=0.076 000825.SZ=0.486 000839.SZ=0.245 "
    "000858.SZ=1.076 000876.SZ=0.093 000878.SZ=0.344 000895.SZ=0.288 000897.SZ=0.136 "
    "000898.SZ=0.528 000900.SZ=0.15 000912.SZ=0.055 000917.SZ=0.105 000927.SZ=0.102 "
    "000932.SZ=0.15 000933.SZ=0.297 000937.SZ=0.293 000951.SZ=0.082 000959.SZ=0.128 "
    "000960.SZ=0.18 000968.SZ=0.118 000969.SZ=0.126 000983.SZ=0.865 000999.SZ=0.14 002001.SZ=0.12 "
    "002024.SZ=1.041 002028.SZ=0.127 002038.SZ=0.12 002122.SZ=0.093 002128.SZ=0.109 "
    "002142.SZ=0.391 002155.SZ=0.125 002194.SZ=0.065 002202.SZ=0.431 002242.SZ=0.078 "
    "002244.SZ=0.07 002269.SZ=0.081 600000.SH=2.057 600004.SH=0.084 600005.SH=0.465 "
    "600006.SH=0.098 600008.SH=0.114 600009.SH=0.3 600010.SH=0.213 600011.SH=0.387 "
    "600015.SH=0.444 600016.SH=2.665 600017.SH=0.056 600018.SH=0.436 600019.SH=0.908 "
    "600022.SH=0.118 600026.SH=0.162 600027.SH=0.103 600028.SH=1.05 600029.SH=0.18 "
    "600030.SH=2.639 600031.SH=0.392 600033.SH=0.108 600036.SH=3.541 600037.SH=0.162 "
    "600048.SH=0.564 600050.SH=1.106 600058.SH=0.149 600066.SH=0.13 600068.SH=0.27 "
    "600085.SH=0.098 600087.SH=0.13 600089.SH=0.613 600096.SH=0.127 600100.SH=0.229 "
    "600102.SH=0.065 600104.SH=0.613 600108.SH=0.144 600109.SH=0.13 600110.SH=0.095 "
    "600111.SH=0.238 600117.SH=0.084 600118.SH=0.076 600123.SH=0.268 600125.SH=0.139 "
    "600132.SH=0.143 600143.SH=0.132 600150.SH=0.185 600151.SH=0.062 600153.SH=0.173 "
    "600158.SH=0.095 600169.SH=0.111 600170.SH=0.1 600176.SH=0.071 600177.SH=0.289 "
    "600183.SH=0.106 600188.SH=0.244 600196.SH=0.26 600208.SH=0.18 600210.SH=0.141 "
    "600216.SH=0.172 600219.SH=0.235 600220.SH=0.129 600221.SH=0.08 600236.SH=0.083 "
    "600251.SH=0.082 600256.SH=0.195 600266.SH=0.119 600269.SH=0.182 600270.SH=0.057 "
    "600271.SH=0.134 600276.SH=0.292 600282.SH=0.073 600299.SH=0.064 600307.SH=0.111 "
    "600308.SH=0.115 600309.SH=0.357 600316.SH=0.105 600320.SH=0.327 600325.SH=0.219 "
    "600331.SH=0.2 600348.SH=0.416 600350.SH=0.063 600352.SH=0.189 600362.SH=0.353 "
    "600376.SH=0.081 600380.SH=0.092 600383.SH=0.494 600395.SH=0.174 600415.SH=0.272 "
    "600418.SH=0.147 600426.SH=0.146 600428.SH=0.124 600432.SH=0.201 600456.SH=0.088 "
    "600489.SH=0.412 600497.SH=0.286 600500.SH=0.153 600508.SH=0.13 600516.SH=0.094 "
    "600518.SH=0.226 600519.SH=1.148 600528.SH=0.171 600547.SH=0.511 600548.SH=0.03 "
    "600549.SH=0.069 600550.SH=0.33 600569.SH=0.098 600582.SH=0.168 600583.SH=0.265 "
    "600585.SH=0.476 600588.SH=0.124 600595.SH=0.153 600596.SH=0.174 600597.SH=0.053 "
    "600598.SH=0.187 600600.SH=0.234 600601.SH=0.159 600611.SH=0.136 600616.SH=0.082 "
    "600631.SH=0.213 600635.SH=0.229 600638.SH=0.139 600639.SH=0.058 600642.SH=0.294 "
    "600643.SH=0.17 600649.SH=0.26 600653.SH=0.139 600655.SH=0.312 600660.SH=0.268 "
    "600663.SH=0.184 600664.SH=0.246 600674.SH=0.108 600675.SH=0.201 600685.SH=0.08 "
    "600688.SH=0.191 600690.SH=0.356 600694.SH=0.23 600717.SH=0.186 600718.SH=0.115 "
    "600737.SH=0.151 600739.SH=0.615 600741.SH=0.268 600748.SH=0.108 600770.SH=0.082 "
    "600779.SH=0.14 600782.SH=0.067 600795.SH=0.36 600804.SH=0.112 600808.SH=0.216 600809.SH=0.1 "
    "600811.SH=0.169 600812.SH=0.172 600816.SH=0.112 600820.SH=0.128 600832.SH=0.324 "
    "600835.SH=0.099 600837.SH=1.413 600839.SH=0.178 600859.SH=0.156 600874.SH=0.044 "
    "600875.SH=0.269 600879.SH=0.134 600881.SH=0.308 600886.SH=0.105 600895.SH=0.178 "
    "600900.SH=1.052 600970.SH=0.112 600997.SH=0.279 601001.SH=0.27 601005.SH=0.037 "
    "601006.SH=0.718 601009.SH=0.445 601088.SH=2.056 601111.SH=0.273 601166.SH=2.525 "
    "601168.SH=0.439 601169.SH=1.509 601186.SH=0.504 601318.SH=2.832 601328.SH=3.038 "
    "601333.SH=0.241 601390.SH=0.578 601398.SH=1.428 601588.SH=0.169 601600.SH=0.496 "
    "601601.SH=1.444 601628.SH=0.851 601666.SH=0.4 601699.SH=0.427 601727.SH=0.106 601766.SH=0.4 "
    "601808.SH=0.172 601857.SH=0.929 601866.SH=0.197 601872.SH=0.139 601898.SH=0.445 "
    "601899.SH=0.364 601918.SH=0.119 601919.SH=0.57 601939.SH=0.997 601958.SH=0.33 "
    "601988.SH=0.503 601991.SH=0.105 601998.SH=0.339"
)

# 300 constituents summing to 99.998, published 2010-01-29.
CSI300_20100129 = (
    "000001.SZ=1.311 000002.SZ=1.759 000009.SZ=0.221 000012.SZ=0.165 000021.SZ=0.112 "
    "000024.SZ=0.304 000027.SZ=0.16 000031.SZ=0.167 000039.SZ=0.23 000046.SZ=0.109 "
    "000059.SZ=0.131 000060.SZ=0.315 000061.SZ=0.162 000063.SZ=0.781 000069.SZ=0.462 "
    "000089.SZ=0.095 000100.SZ=0.248 000157.SZ=0.359 000301.SZ=0.103 000338.SZ=0.399 "
    "000401.SZ=0.229 000402.SZ=0.406 000422.SZ=0.22 000423.SZ=0.296 000425.SZ=0.229 "
    "000488.SZ=0.14 000527.SZ=0.478 000528.SZ=0.179 000538.SZ=0.241 000559.SZ=0.054 "
    "000562.SZ=0.244 000568.SZ=0.475 000612.SZ=0.136 000623.SZ=0.566 000625.SZ=0.164 "
    "000630.SZ=0.227 000631.SZ=0.053 000651.SZ=0.612 000652.SZ=0.155 000667.SZ=0.14 "
    "000680.SZ=0.14 000685.SZ=0.107 000686.SZ=0.165 000690.SZ=0.163 000709.SZ=0.46 "
    "000717.SZ=0.124 000718.SZ=0.106 000728.SZ=0.29 000729.SZ=0.199 000758.SZ=0.099 "
    "000768.SZ=0.331 000778.SZ=0.21 000780.SZ=0.101 000783.SZ=0.445 000792.SZ=0.479 "
    "000793.SZ=0.11 000800.SZ=0.327 000807.SZ=0.137 000822.SZ=0.075 000825.SZ=0.424 "
    "000839.SZ=0.266 000858.SZ=1.078 000876.SZ=0.094 000878.SZ=0.305 000895.SZ=0.33 "
    "000897.SZ=0.13 000898.SZ=0.424 000900.SZ=0.141 000917.SZ=0.138 000927.SZ=0.092 "
    "000932.SZ=0.131 000933.SZ=0.263 000937.SZ=0.256 000951.SZ=0.091 000959.SZ=0.119 "
    "000960.SZ=0.186 000968.SZ=0.09 000969.SZ=0.124 000983.SZ=0.734 000999.SZ=0.168 "
    "002001.SZ=0.128 002007.SZ=0.196 002024.SZ=0.993 002028.SZ=0.139 002122.SZ=0.098 "
    "002128.SZ=0.103 002142.SZ=0.365 002155.SZ=0.133 002202.SZ=0.513 002242.SZ=0.079 "
    "002244.SZ=0.1 002275.SZ=0.019 600000.SH=2.022 600004.SH=0.096 600005.SH=0.413 "
    "600006.SH=0.091 600008.SH=0.113 600009.SH=0.348 600010.SH=0.201 600011.SH=0.387 "
    "600015.SH=0.627 600016.SH=2.648 600017.SH=0.063 600018.SH=0.449 600019.SH=0.775 "
    "600022.SH=0.128 600026.SH=0.164 600027.SH=0.102 600028.SH=0.925 600029.SH=0.196 "
    "600030.SH=2.554 600031.SH=0.357 600033.SH=0.126 600036.SH=3.235 600037.SH=0.21 "
    "600048.SH=0.653 600050.SH=1.16 600058.SH=0.142 600062.SH=0.161 600066.SH=0.129 "
    "600068.SH=0.407 600085.SH=0.123 600087.SH=0.118 600089.SH=0.601 600096.SH=0.141 "
    "600100.SH=0.311 600102.SH=0.07 600104.SH=0.513 600108.SH=0.158 600109.SH=0.121 "
    "600111.SH=0.254 600118.SH=0.09 600123.SH=0.259 600125.SH=0.153 600132.SH=0.126 "
    "600143.SH=0.128 600150.SH=0.183 600151.SH=0.065 600153.SH=0.17 600158.SH=0.123 "
    "600161.SH=0.112 600166.SH=0.227 600169.SH=0.102 600170.SH=0.098 600176.SH=0.076 "
    "600177.SH=0.308 600183.SH=0.141 600188.SH=0.236 600196.SH=0.29 600208.SH=0.163 "
    "600210.SH=0.159 600216.SH=0.178 600219.SH=0.216 600220.SH=0.129 600221.SH=0.138 "
    "600236.SH=0.086 600239.SH=0.107 600246.SH=0.091 600251.SH=0.088 600256.SH=0.241 "
    "600266.SH=0.11 600269.SH=0.192 600270.SH=0.065 600271.SH=0.198 600276.SH=0.289 "
    "600282.SH=0.067 600307.SH=0.095 600309.SH=0.36 600312.SH=0.15 600316.SH=0.11 600320.SH=0.319 "
    "600325.SH=0.194 600331.SH=0.18 600348.SH=0.345 600350.SH=0.07 600352.SH=0.219 "
    "600362.SH=0.325 600369.SH=0.121 600376.SH=0.076 600380.SH=0.105 600383.SH=0.457 "
    "600395.SH=0.152 600415.SH=0.295 600418.SH=0.138 600426.SH=0.143 600428.SH=0.13 "
    "600432.SH=0.179 600456.SH=0.091 600489.SH=0.379 600497.SH=0.26 600500.SH=0.165 "
    "600508.SH=0.124 600516.SH=0.107 600517.SH=0.124 600518.SH=0.248 600519.SH=1.238 "
    "600528.SH=0.159 600547.SH=0.454 600549.SH=0.085 600550.SH=0.349 600569.SH=0.088 "
    "600582.SH=0.145 600583.SH=0.258 600585.SH=0.421 600588.SH=0.136 600595.SH=0.138 "
    "600596.SH=0.181 600597.SH=0.077 600598.SH=0.193 600600.SH=0.237 600601.SH=0.173 "
    "600611.SH=0.152 600631.SH=0.236 600635.SH=0.228 600638.SH=0.132 600639.SH=0.059 "
    "600642.SH=0.291 600643.SH=0.17 600648.SH=0.022 600649.SH=0.266 600655.SH=0.345 "
    "600657.SH=0.08 600660.SH=0.248 600663.SH=0.177 600664.SH=0.264 600674.SH=0.115 "
    "600675.SH=0.196 600685.SH=0.077 600688.SH=0.189 600690.SH=0.315 600694.SH=0.238 "
    "600717.SH=0.198 600718.SH=0.143 600737.SH=0.148 600739.SH=0.631 600741.SH=0.186 "
    "600748.SH=0.107 600779.SH=0.139 600782.SH=0.065 600795.SH=0.366 600804.SH=0.142 "
    "600808.SH=0.203 600809.SH=0.101 600811.SH=0.188 600812.SH=0.164 600816.SH=0.113 "
    "600820.SH=0.136 600832.SH=0.382 600835.SH=0.1 600837.SH=1.36 600839.SH=0.2 600859.SH=0.154 "
    "600874.SH=0.045 600875.SH=0.289 600879.SH=0.165 600881.SH=0.301 600886.SH=0.105 "
    "600895.SH=0.177 600900.SH=1.094 600970.SH=0.115 600997.SH=0.245 601001.SH=0.237 "
    "601006.SH=0.738 601009.SH=0.407 601088.SH=1.89 601099.SH=0.097 601107.SH=0.067 "
    "601111.SH=0.311 601166.SH=2.19 601168.SH=0.411 601169.SH=1.32 601186.SH=0.51 601318.SH=2.596 "
    "601328.SH=2.885 601333.SH=0.25 601390.SH=0.571 601398.SH=1.381 601588.SH=0.161 "
    "601600.SH=0.473 601601.SH=1.352 601618.SH=0.31 601628.SH=0.789 601666.SH=0.356 "
    "601668.SH=0.497 601699.SH=0.361 601727.SH=0.109 601766.SH=0.416 601808.SH=0.172 "
    "601857.SH=0.956 601866.SH=0.212 601872.SH=0.155 601898.SH=0.426 601899.SH=0.851 "
    "601918.SH=0.109 601919.SH=0.582 601939.SH=0.989 601958.SH=0.319 601988.SH=0.511 "
    "601991.SH=0.115 601998.SH=0.31"
)


PUBLICATIONS: dict[str, str] = {
    "20091130": CSI300_20091130,
    "20091231": CSI300_20091231,
    "20100129": CSI300_20100129,
}


def _rows(published: str) -> tuple[tuple[str, float], ...]:
    """Split one publication's `code=weight` text back into rows."""
    return tuple((pair.split("=")[0], float(pair.split("=")[1])) for pair in published.split())


class _PublicationTransport:
    """Serves whichever publication the request's month window asks for.

    Doubles Tushare's `post(payload) -> dict` Protocol. Not the shared
    `fake_tushare_transport` fixture, which answers every request with one canned body: this
    file drives a real backfill loop, so the transport has to route on `params`.
    """

    def __init__(self, publications: dict[str, tuple[tuple[str, float], ...]]) -> None:
        self.publications = publications
        self.requests: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = payload["params"]
        self.requests.append(params)
        served = [
            (day, rows)
            for day, rows in self.publications.items()
            if params["start_date"] <= day <= params["end_date"]
        ]
        items = [
            [params["index_code"], con_code, day, weight]
            for day, rows in served
            for con_code, weight in rows
        ]
        return {
            "code": 0,
            "msg": "",
            "data": {
                "fields": list(RESPONSE_FIELDS),
                "items": items,
                TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
            },
        }


FETCHED_AT = datetime(2010, 2, 5, 12, 0, tzinfo=UTC)
AS_OF = datetime(2010, 2, 5, 12, 0, tzinfo=UTC)
MAX_STALENESS = timedelta(days=45)
"""Wider than a publication interval, narrower than two. This dataset publishes monthly, so
anything under ~35 days would block a completely healthy panel for most of every month."""


MONTH_ENDS: dict[str, datetime] = {
    # The `as_of` a real backfill would use for each month: late on that month's last calendar
    # day, in Asia/Shanghai. Earlier than the publication's own 16:30 availability and the
    # point-in-time filter drops every row -- correctly, because on 2009-11-15 the November
    # publication genuinely did not exist yet.
    "20091130": datetime(2009, 11, 30, 15, 0, tzinfo=UTC),
    "20091231": datetime(2009, 12, 31, 15, 0, tzinfo=UTC),
    "20100129": datetime(2010, 1, 31, 15, 0, tzinfo=UTC),
}


def _months(*days: str) -> tuple[datetime, ...]:
    """One `as_of` inside each named publication's month, after the publication itself."""
    return tuple(MONTH_ENDS[day] for day in days)


def _batches(
    provider: TushareProvider, index_code: str, *days: str
) -> tuple[ColumnarPanelBatch, ...]:
    return tuple(
        provider.fetch_panel(
            ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(index_code,))
        )
        for as_of in _months(*days)
    )


@pytest.fixture
def provider() -> TushareProvider:
    transport = _PublicationTransport({day: _rows(text) for day, text in PUBLICATIONS.items()})
    return TushareProvider(token="secret-token", transport=transport, clock=lambda: FETCHED_AT)


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    return PanelStore(tmp_path / "panel")


def _ingest(store: PanelStore, provider: TushareProvider) -> None:
    """Two years, each written from its own month fetches."""
    write_index_weights(store, _batches(provider, CSI300_INDEX_CODE, "20091130", "20091231"))
    write_index_weights(store, _batches(provider, CSI300_INDEX_CODE, "20100129"))


def _membership(store: PanelStore) -> Any:
    return load_index_membership(
        store,
        index_code=CSI300_INDEX_CODE,
        years=(2009, 2010),
        as_of=AS_OF,
        max_staleness=MAX_STALENESS,
    )


# --------------------------------------------------------------------------------------
# The round trip
# --------------------------------------------------------------------------------------


def test_a_year_is_assembled_from_month_fetches_and_read_back_whole(
    store: PanelStore, provider: TushareProvider
) -> None:
    """One request is one index for one month; one partition is one year.

    Nothing here is compressed, so what comes back is what was published: three publications,
    898 constituent rows, on the three dates the endpoint served.
    """
    _ingest(store, provider)
    membership = _membership(store)

    assert membership.publication_dates == (
        date(2009, 11, 30),
        date(2009, 12, 31),
        date(2010, 1, 29),
    )
    assert [entry.constituent_count for entry in membership.publications] == [300, 298, 300]
    assert membership.covered_from == date(2009, 11, 30)
    assert membership.covered_through == date(2010, 1, 29)


def test_the_published_totals_survive_the_round_trip_unnormalised(
    store: PanelStore, provider: TushareProvider
) -> None:
    """99.997 / 99.993 / 99.998 as published. A store that renormalised to 100 would destroy
    the only statement the publisher makes about its own rounding."""
    _ingest(store, provider)
    totals = [round(entry.published_total, 6) for entry in _membership(store).publications]
    assert totals == [99.997, 99.993, 99.998]


def test_a_publication_can_carry_fewer_names_than_the_index_is_called(
    store: PanelStore, provider: TushareProvider
) -> None:
    """The 2009-12-31 publication is 298 names, and both missing ones are named.

    A contract that asserted 300 would refuse this real partition outright.
    """
    _ingest(store, provider)
    membership = _membership(store)
    december = membership.constituents_on(date(2009, 12, 31))
    november = membership.constituents_on(date(2009, 11, 30))

    assert len(december.members) == 298
    assert set(november.members) - set(december.members) == {"600001.SH", "600357.SH"}


# --------------------------------------------------------------------------------------
# Composition and weights, kept apart
# --------------------------------------------------------------------------------------


def test_a_mid_month_question_says_which_publication_it_came_from(
    store: PanelStore, provider: TushareProvider
) -> None:
    """The acceptance: staleness is readable out of the store, not inferred by the caller.

    2009-12-15 is fifteen days after the publication its answer comes from, and the weights are
    a month-end snapshot that has been drifting with prices for every one of them.
    """
    _ingest(store, provider)
    membership = _membership(store)

    weights = membership.weights_on(date(2009, 12, 15))
    assert weights.as_published_on == date(2009, 11, 30)
    assert weights.days_since_publication == 15
    assert weights.is_as_published is False
    assert weights.weight_of("000001.SZ") == pytest.approx(1.372)

    on_the_day = membership.weights_on(date(2009, 11, 30))
    assert on_the_day.is_as_published is True
    assert on_the_day.days_since_publication == 0


def test_the_composition_answer_is_a_different_type_from_the_weights_answer(
    store: PanelStore, provider: TushareProvider
) -> None:
    """`constituents_on` cannot hand back a stale number because it hands back no number."""
    _ingest(store, provider)
    membership = _membership(store)

    composition = membership.constituents_on(date(2009, 12, 15))
    assert "600001.SH" in composition.members
    assert not hasattr(composition, "weight_of")
    assert membership.weights_on(date(2009, 12, 15)).composition() == composition


def test_a_day_past_the_last_stored_publication_is_refused(
    store: PanelStore, provider: TushareProvider
) -> None:
    """2010-02-05 is inside the read's `as_of` and past its last publication, and those are
    different questions. Carrying 2010-01-29 forward would assert that no rebalance happened in
    February, and 166 of the 630 measured transitions changed the membership."""
    _ingest(store, provider)
    with pytest.raises(IndexMembershipHorizonError, match="after"):
        _membership(store).weights_on(date(2010, 2, 1))


# --------------------------------------------------------------------------------------
# Rebalances
# --------------------------------------------------------------------------------------


def test_the_stored_years_name_who_joined_and_who_left(
    store: PanelStore, provider: TushareProvider
) -> None:
    """Two real transitions, and they are not the same shape.

    December removed two names and replaced neither -- which is how that publication came to
    carry 298 -- while the January review swapped 18 in for 16 out, so the count went back to
    300. `is_one_for_one` is what tells them apart.
    """
    _ingest(store, provider)
    december, january = _membership(store).rebalances()

    assert december.publication == date(2009, 12, 31)
    assert december.added == ()
    assert december.removed == ("600001.SH", "600357.SH")
    assert december.is_one_for_one is False

    assert january.publication == date(2010, 1, 29)
    assert len(january.added) == 18
    assert len(january.removed) == 16
    assert "601668.SH" in january.added
    assert "600110.SH" in january.removed
    assert january.is_one_for_one is False


# --------------------------------------------------------------------------------------
# The registry join
# --------------------------------------------------------------------------------------


def _registry() -> Any:
    """A `StockUniverse` carrying the real registry rows for the names this file touches."""
    return build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=(
            SecurityLifecycle(
                ts_code="600001.SH",
                exchange="SSE",
                listed_on=date(1998, 1, 22),
                delisted_on=date(2009, 12, 29),
            ),
            SecurityLifecycle(
                ts_code="600357.SH",
                exchange="SSE",
                listed_on=date(2002, 9, 6),
                delisted_on=date(2009, 12, 29),
            ),
        ),
    )


def test_a_forward_filled_membership_can_disagree_with_the_registry(
    store: PanelStore, provider: TushareProvider
) -> None:
    """The measured cost of forward-filling a composition, out of the store.

    `600001.SH` 邯郸钢铁 is in the 2009-11-30 publication, its registry `delist_date` is
    2009-12-29, and the next publication is 2009-12-31 -- so a question about 2009-12-30 names
    a security that had already gone. 38 constituent terminations across the corpus fall inside
    such a window. Composition survives a forward fill much better than the weights do; it does
    not survive it perfectly, and this module says so rather than claiming it does.
    """
    _ingest(store, provider)
    universe = _registry()
    membership = _membership(store)

    composition = membership.constituents_on(date(2009, 12, 30))
    assert composition.as_published_on == date(2009, 11, 30)
    assert "600001.SH" in composition.members
    assert universe.is_listed("600001.SH", date(2009, 12, 30)) is False

    # On the publication day itself both names were still listed, so the disagreement is
    # entirely a product of the forward fill rather than of the publication.
    report = constituent_listing_report(membership.publications[0], universe=universe)
    assert report.not_listed == ()
    assert "600001.SH" not in report.unknown_to_registry


def test_the_registry_join_reports_rather_than_refusing(
    store: PanelStore, provider: TushareProvider
) -> None:
    """A constituent the registry has never heard of is a fact about the registry.

    Every constituent of the 2009-11-30 publication except the two named above is absent from
    this deliberately tiny universe, and the report says so instead of raising -- which is what
    lets a caller see `990018.SH`, the real code that is in eighteen 沪深300 publications and in
    neither half of `stock_basic`.
    """
    _ingest(store, provider)
    report = constituent_listing_report(_membership(store).publications[0], universe=_registry())

    assert len(report.unknown_to_registry) == 298
    assert report.not_listed == ()
    assert report.is_clean is False


# --------------------------------------------------------------------------------------
# The overwrite guard
# --------------------------------------------------------------------------------------


def _synthetic_batch(
    provider: TushareProvider, index_code: str, day: str, codes: tuple[str, ...]
) -> ColumnarPanelBatch:
    """A publication for another index, in the same year, with weights that add up."""
    weight = 100.0 / len(codes)
    transport = _PublicationTransport({day: tuple((code, weight) for code in codes)})
    other = TushareProvider(token="secret-token", transport=transport, clock=lambda: FETCHED_AT)
    (batch,) = _batches(other, index_code, day)
    return batch


def test_writing_one_index_over_a_partition_that_holds_another_is_refused(
    store: PanelStore, provider: TushareProvider
) -> None:
    """`V2-P1-004`'s `exchange` failure, in this dataset's shape.

    `PanelStore`'s key is `(dataset, year)` with no index dimension and a partition is replaced
    whole, so the obvious backfill -- one call per index -- would leave 2009 holding only
    中证500. The reads were already fail-closed (a 沪深300 load would block on
    `subject_missing`), and a silent destructive write that returns a partition reference is not
    something a downstream check should have to catch.
    """
    _ingest(store, provider)
    intruder = _synthetic_batch(provider, CSI500_INDEX_CODE, "20091231", ("000001.SZ", "000002.SZ"))

    with pytest.raises(PanelBatchError, match="would drop"):
        write_index_weights(store, (intruder,))

    assert _membership(store).covered_through == date(2010, 1, 29)


def test_two_indices_written_together_share_the_year(
    store: PanelStore, provider: TushareProvider
) -> None:
    """The write the guard is steering callers towards: one batch sequence, every index in it."""
    csi500 = tuple(
        _synthetic_batch(provider, CSI500_INDEX_CODE, day, ("000001.SZ", "000002.SZ"))
        for day in ("20091130", "20091231")
    )
    write_index_weights(
        store, (*_batches(provider, CSI300_INDEX_CODE, "20091130", "20091231"), *csi500)
    )

    assert load_index_membership(
        store,
        index_code=CSI500_INDEX_CODE,
        years=(2009,),
        as_of=AS_OF,
        max_staleness=MAX_STALENESS,
    ).covered_through == date(2009, 12, 31)
    assert load_index_membership(
        store,
        index_code=CSI300_INDEX_CODE,
        years=(2009,),
        as_of=AS_OF,
        max_staleness=MAX_STALENESS,
    ).covered_through == date(2009, 12, 31)


def test_a_batch_from_another_dataset_is_refused_by_name(
    store: PanelStore, provider: TushareProvider
) -> None:
    (batch,) = _batches(provider, CSI300_INDEX_CODE, "20091130")
    disguised = ColumnarPanelBatch(
        provider_id=batch.provider_id,
        dataset="daily",
        kind=batch.kind,
        as_of=batch.as_of,
        fetched_at=batch.fetched_at,
        status="success",
        subjects=batch.subjects,
        timeline=batch.timeline,
        columns=batch.columns,
    )
    with pytest.raises(PanelBatchError, match="expected the 'index_weight' dataset"):
        write_index_weights(store, (disguised,))


# --------------------------------------------------------------------------------------
# What a hole looks like on the read side
# --------------------------------------------------------------------------------------


def test_a_year_whose_middle_month_never_landed_is_refused_on_load(
    store: PanelStore, provider: TushareProvider
) -> None:
    """The check that pays for `index_weight_requirement`'s waived `required_dates`.

    November and January land, December does not. Every readiness check passes -- the partitions
    exist, are profiled, carry the subject and the fields, and are fresh -- and the month rule is
    what refuses it. Without that rule, every day of December would be answered from a
    publication that by then was up to two months old, silently.
    """
    write_index_weights(store, _batches(provider, CSI300_INDEX_CODE, "20091130"))
    write_index_weights(store, _batches(provider, CSI300_INDEX_CODE, "20100129"))

    with pytest.raises(IndexMembershipError, match="no publication for 2009-12"):
        _membership(store)


def test_a_year_that_was_never_ingested_blocks_rather_than_narrowing_the_answer(
    store: PanelStore, provider: TushareProvider
) -> None:
    """A skipped partition is not an index that published nothing that year."""
    write_index_weights(store, _batches(provider, CSI300_INDEX_CODE, "20091130", "20091231"))

    with pytest.raises(PanelStorageError, match=r"000300\.SH's composition cannot be read"):
        _membership(store)


def test_a_year_that_holds_another_index_blocks_on_the_missing_subject(
    store: PanelStore, provider: TushareProvider
) -> None:
    """The requirement names the index, so "this year holds 沪深300 but not 中证500" is a
    blocked read with a `subject_missing` code rather than a mapping that came back empty.

    Both refuse; only one of them says which of the two things went wrong, and the readiness
    codes are what `V2-P1-012`'s health report and `V2-P1-013`'s gate branch on.
    """
    _ingest(store, provider)

    with pytest.raises(PanelStorageError, match="subject_missing"):
        load_index_membership(
            store,
            index_code=CSI500_INDEX_CODE,
            years=(2009,),
            as_of=AS_OF,
            max_staleness=MAX_STALENESS,
        )


def test_reading_no_years_is_refused(store: PanelStore) -> None:
    with pytest.raises(IndexMembershipError, match="at least one year"):
        load_index_membership(
            store,
            index_code=CSI300_INDEX_CODE,
            years=(),
            as_of=AS_OF,
            max_staleness=MAX_STALENESS,
        )
