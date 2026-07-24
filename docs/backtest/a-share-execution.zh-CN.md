# A 股回放执行假设

OpenAlpha CN v1 的回测执行器用于研究验证，不是券商撮合仿真，也不连接真实交易。

## 已实现规则

- 现金股票卖出遵守 T+1；
- 主板等普通买入数量按 100 股整数倍；
- 科创板买入最低 200 股，超过 200 股后可按 1 股递增；
- 主板默认 10%、ST 默认 5%、科创板/创业板 20%、北交所 30% 涨跌幅参数；
- 停牌不可成交；
- 一字涨停不可买入，一字跌停不可卖出；
- 默认佣金、最低佣金、过户费和卖出印花税均可配置。

默认卖出印花税率使用 `0.0005`。依据：

- [国家税务总局：证券交易印花税自 2023-08-28 起减半征收](https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448)
- [印花税法：证券交易印花税对出让方征收](https://guangdong.chinatax.gov.cn/gdsw/dgsw_gkwj/2023-03/07/content_48aca236e3e046b88722793056faa9d4.shtml)

交易制度参考：

- [深交所 2026 年交易规则发布通知](https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html)
- [上交所科创板交易制度说明](https://edu.sse.com.cn/tib/)
- [上交所股票申报数量说明](https://www.sse.com.cn/lawandrules/guide/stock/jyglywznylc/tz/c/c_20230209_5716007.shtml)

券商佣金、最低收费、过户费适用范围和交易所规则可能变化。生产研究必须把实际账户和
研究日期对应的参数写入 `RunManifest` 配置摘要，不能把仓库默认值当成永久规则。

## 冻结回放语料

`tests/fixtures/replay/a-share-v1-corpus.json` 是 CC0 合成夹具：

- 60 个合成交易会话；
- 每个会话 5 个事件，共 300 个事件；
- 覆盖涨停、炸板、连板、公告、题材、催化和资金证据；
- 每个案例有独立 `as_of` 和未来观察窗口；
- 不包含真实行情或第三方原始数据。

生成并验证：

```powershell
uv run python scripts/generate_replay_corpus.py
uv run pytest tests/replay/test_frozen_corpus.py
```

测试要求 300 个案例全部成功、两次结果一致、已知前视违规为零、所有归因与净主动收益对账。
