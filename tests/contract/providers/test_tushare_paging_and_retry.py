"""The cap escape hatch, the retry policy, and the probe hooks (`V2-P1-018`).

Three things that were each a hole rather than a bug, and all three live on the same seam --
what `TushareProvider` does between building a request and handing rows upward.

**The cap escape hatch.** `stk_limit`'s whole-market cross section was 7,734 rows on
2026-08-10 against a measured 7,800-row cap, growing +2.231 rows per session over the thirteen
session steps from 2026-07-22 -- 29.6 sessions of headroom. `_check_response_completeness`
refuses at the cap, correctly, and until this issue that refusal was terminal: no face of this
repository could split the request, and the registry cannot enumerate this endpoint's subjects
anyway (5,878 codes against 7,733, with 2,194 funds absent from it). So the fallback is
`limit`/`offset` paging, measured against the live endpoint and reactive -- it runs only after
the guard has already refused a one-shot response.

**The retry policy.** `ProviderFailure.retryable` had existed since the first provider and had
**no consumer anywhere in the repository**: a transient socket error 27 seconds into a
45-minute build voided the whole build, under the category `invalid_response` with
`retryable=False`, because a bare `URLError` escaping the transport was caught by the decode
clause. And `rate_limit`, a declared category, had never once been produced -- a live probe on
2026-08-10 (600 concurrent `income` requests, 40 threads, 16.4s) returned 100 responses of
`code=40203` naming a 500-per-minute interface quota, every one of which was classified
`upstream`.

**The probe hooks.** `openalpha doctor --probe` sent **no request at all** for nine of this
table's fifteen datasets. Those tests live in `tests/unit/test_cli.py` beside the rest of
`doctor`'s; what is pinned here is the provider-side half -- that the hooks answer for every
dataset and that their answers are requests the real builders accept.

Nothing in this file touches the network. Every response body is shaped like a real one and the
row counts are the measured ones.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.domain.price_limits import KNOWN_SUSPENSION_LIMITATIONS, PRICE_LIMIT_DATASET
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    MAX_TUSHARE_PAGES,
    TUSHARE_DATASETS,
    TUSHARE_RATE_LIMIT_CODE,
    TUSHARE_RATE_LIMIT_DELAY,
    TUSHARE_STK_LIMIT_ROW_CAP,
    TUSHARE_TRANSPORT_ATTEMPTS,
    ClockStrategy,
    TushareDatasetDescriptor,
    TushareProvider,
    TushareResponseTruncated,
    _paged_params,
    _trade_date_params,
)

FETCHED_AT = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on 2026-08-07, so that session's 16:30 availability has passed."""

LIMIT_FIELDS = ["trade_date", "ts_code", "up_limit", "down_limit"]

MEASURED_CROSS_SECTION = 7733
"""`stk_limit(trade_date=20260807)`'s own row count, measured. 67 under the cap on that day."""


def _descriptor(dataset: str) -> TushareDatasetDescriptor:
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == dataset)
    return descriptor


def _bands(count: int, *, start: int = 0) -> list[list[Any]]:
    """`count` distinct band rows, one per synthetic security.

    Distinct rather than repeated because two of the guards under test are about *duplicate*
    rows, and a fixture built from one row copied N times cannot tell a page that overlapped
    from one that did not.
    """
    return [
        ["20260807", f"{600000 + index:06d}.SH", 12.03 + index / 100.0, 9.85]
        for index in range(start, start + count)
    ]


def _request(dataset: str = PRICE_LIMIT_DATASET) -> ProviderRequest:
    return ProviderRequest(dataset=dataset, as_of=FETCHED_AT)


class PagingTransport:
    """A transport that serves one whole cross section and honours `limit`/`offset`.

    The behaviour measured on the live endpoint, restated as a double: a request with no
    `limit` gets the endpoint's cap-truncated prefix with `has_more=True`, and a request with a
    `limit` gets exactly that slice with `has_more` equal to whether anything follows it. That
    is the *only* shape that makes the paged and un-paged answers comparable, which is what
    `test_the_paged_and_the_one_shot_batch_are_the_same_batch` needs.
    """

    def __init__(self, rows: list[list[Any]], *, cap: int = TUSHARE_STK_LIMIT_ROW_CAP) -> None:
        self.rows = rows
        self.cap = cap
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        params = payload["params"]
        offset = int(params.get("offset", 0))
        limit = int(params["limit"]) if "limit" in params else self.cap
        window = self.rows[offset : offset + limit]
        return {
            "code": 0,
            "msg": "",
            "data": {
                "fields": LIMIT_FIELDS,
                "items": window,
                "has_more": offset + len(window) < len(self.rows),
            },
        }


class StaticTransport:
    """Answers every request with the same body, whatever `limit`/`offset` it carries."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        index = min(len(self.payloads) - 1, len(self.responses) - 1)
        return self.responses[index]


def _response(items: list[list[Any]], *, has_more: bool) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": LIMIT_FIELDS, "items": items, "has_more": has_more},
    }


def _provider(transport: Any, **kwargs: Any) -> TushareProvider:
    return TushareProvider(
        token="secret-token", transport=transport, clock=lambda: FETCHED_AT, **kwargs
    )


# --- the descriptor invariant ---------------------------------------------------------------


def test_exactly_one_descriptor_declares_a_page_size_and_it_is_the_one_running_out_of_room() -> (
    None
):
    """Pinned by equality, not by membership, so a later descriptor cannot acquire paging
    without this test being edited -- the shape `requires_truncation_flag` is already pinned
    with, and for the same reason: `page_size` is a claim that `offset` was *measured* to
    partition this endpoint's answer, and it is measurably false for at least one endpoint on
    this API (`namechange`: two 10,000-row pages return 380 duplicated rows and lose one the
    per-`ts_code` query finds)."""
    paged = {entry.dataset for entry in TUSHARE_DATASETS if entry.page_size is not None}

    assert paged == {PRICE_LIMIT_DATASET}
    assert _descriptor(PRICE_LIMIT_DATASET).page_size == 4000


def test_a_page_size_without_the_truncation_flag_is_refused_at_construction() -> None:
    """The loop stops on `has_more=False`. On a descriptor that tolerates an absent flag, a
    response that simply omitted it would end the loop as though it were the last page -- the
    silent truncation the guard exists to refuse, reintroduced by the mechanism meant to
    prevent it."""
    with pytest.raises(ValueError, match="does not demand has_more"):
        TushareDatasetDescriptor(
            dataset="probe",
            kind="probe",
            date_field="trade_date",
            clock=ClockStrategy.daily_close,
            params_builder=_trade_date_params,
            source_uri_template="tushare://{dataset}/{subject}/{date}",
            max_rows_per_response=1000,
            requires_truncation_flag=False,
            page_size=500,
        )


@pytest.mark.parametrize("cap", [None, 500, 400])
def test_a_page_size_at_or_above_the_measured_cap_is_refused_at_construction(
    cap: int | None,
) -> None:
    """A page at the cap cannot be told apart from one the cap truncated, so a `page_size` that
    could reach it would page straight past the witness it depends on. `None` is refused for
    the same reason at the limit: a descriptor with no measured cap has no ceiling to sit
    under."""
    with pytest.raises(ValueError, match="cannot be told apart"):
        TushareDatasetDescriptor(
            dataset="probe",
            kind="probe",
            date_field="trade_date",
            clock=ClockStrategy.daily_close,
            params_builder=_trade_date_params,
            source_uri_template="tushare://{dataset}/{subject}/{date}",
            max_rows_per_response=cap,
            requires_truncation_flag=True,
            page_size=500,
        )


# --- the fallback itself --------------------------------------------------------------------


def test_a_cross_section_that_still_fits_is_fetched_in_exactly_one_request() -> None:
    """Reactive, not preventive: until the cap binds, a paged descriptor sends the request it
    has always sent. Preventive paging would have doubled `stk_limit`'s request count on every
    build (145 sessions, ~330s to ~660s) from the day it was switched on until the day it was
    needed."""
    transport = PagingTransport(_bands(MEASURED_CROSS_SECTION))
    batch = _provider(transport).fetch_panel(_request())

    assert len(transport.payloads) == 1
    assert "limit" not in transport.payloads[0]["params"]
    assert len(batch.subjects) == MEASURED_CROSS_SECTION


def test_a_cross_section_past_the_cap_is_fetched_whole_through_the_pages() -> None:
    """The failure this issue exists for: 7,800 rows is the cap, so a market of 7,900 refuses
    permanently on the one-shot path and every `panel build --dataset stk_limit` for the year
    fails with `exit 4` forever. Here it comes back whole, in offset order, from three pages of
    4,000."""
    transport = PagingTransport(_bands(7900))

    batch = _provider(transport).fetch_panel(_request())

    assert len(batch.subjects) == 7900
    assert batch.subjects[0] == "600000.SH"
    assert batch.subjects[-1] == "607899.SH"
    # One refused one-shot request, then ceil(7900 / 4000) = 2 pages.
    assert len(transport.payloads) == 3
    assert [p["params"].get("offset") for p in transport.payloads[1:]] == ["0", "4000"]


def test_the_paged_and_the_one_shot_batch_are_the_same_batch() -> None:
    """**The question a partition assembled from several fetches has to answer.**

    `ColumnarPanelBatch.content_digest` covers the header, every column in order and the
    `source_uri`; `panel/store.py::_content_hash` covers the dataset, the year, the column
    names and types, and the rows. Neither can see how many HTTP requests produced them -- but
    both can see row *order*, and a fallback that reassembled the cross section in a different
    order would change both hashes and make every stored partition's provenance depend on
    whether the market happened to be over the cap that day.

    It does not, and the reason is a measurement rather than an argument: on the live endpoint,
    `limit=4000 offset=0` followed by `limit=4000 offset=4000` returns a 7,733-element sequence
    **element-for-element identical** to the un-paged 7,733-row response for 2026-08-07. This
    asserts the consequence -- same digest, same columns, same subjects -- against a transport
    that reproduces that property, with the clock pinned so `fetched_at` cannot differ.

    The row count is held fixed and only the *endpoint's* cap moves, because "same rows, fewer
    round trips" is the entire claim -- and because the first version of this test varied the
    row count instead, which pushed both sides past the descriptor's own 7,800 cap and compared
    two paged fetches with each other. The premise is therefore asserted, not assumed.
    """
    rows = _bands(MEASURED_CROSS_SECTION)
    one_shot = PagingTransport(rows, cap=10_000)
    paging = PagingTransport(rows, cap=7600)
    unpaged = _provider(one_shot).fetch_panel(_request())
    paged = _provider(paging).fetch_panel(_request())

    assert len(one_shot.payloads) == 1
    assert len(paging.payloads) == 3
    assert unpaged.content_digest == paged.content_digest
    assert unpaged.subjects == paged.subjects
    assert unpaged.source_uri == paged.source_uri
    assert [column.values for column in unpaged.columns] == [
        column.values for column in paged.columns
    ]


def test_a_dataset_with_no_page_size_still_refuses_at_the_cap() -> None:
    """The guard is unchanged for everything else. `suspend_d` declares no `page_size`, so its
    cap refusal is terminal exactly as before -- which is right, because nobody has measured
    that `offset` partitions that endpoint's answer."""
    transport = PagingTransport(_bands(6000), cap=6000)
    descriptor = _descriptor("daily")
    assert descriptor.page_size is None

    with pytest.raises(TushareResponseTruncated, match="measured per-response cap of 6000"):
        _provider(transport).fetch_panel(ProviderRequest(dataset="daily", as_of=FETCHED_AT))


def test_pages_that_serve_the_same_row_twice_are_refused() -> None:
    """The check `namechange`'s measured paging failure would have tripped: two pages of that
    endpoint return 14,166 rows of which 380 are exact duplicates, *and* lose a record the
    per-`ts_code` query returns. Duplication is the observable half, and it is observable here
    -- before a single row reaches a partition."""
    overlapping = _bands(4000)
    transport = StaticTransport(
        _response(_bands(TUSHARE_STK_LIMIT_ROW_CAP), has_more=True),
        _response(overlapping, has_more=True),
        _response(overlapping, has_more=False),
    )

    with pytest.raises(ProviderFailure, match="served the same row twice"):
        _provider(transport).fetch_panel(_request())


def test_a_page_that_never_reports_the_last_one_is_refused_at_the_bound() -> None:
    """An answer assembled from a bounded number of pages that never ended is the same short
    read a truncated single response would have been, so it is refused rather than returned."""
    transport = PagingTransport(_bands(4000 * (MAX_TUSHARE_PAGES + 4)))

    with pytest.raises(ProviderFailure, match=f"reached {MAX_TUSHARE_PAGES} pages"):
        _provider(transport).fetch_panel(_request())


def test_a_page_that_drops_the_truncation_flag_is_refused() -> None:
    """A missing `has_more` on the one-shot path is a schema change; on a page it is worse,
    because `flag is not True` would end the loop and store a prefix."""
    transport = StaticTransport(
        _response(_bands(TUSHARE_STK_LIMIT_ROW_CAP), has_more=True),
        {"code": 0, "msg": "", "data": {"fields": LIMIT_FIELDS, "items": _bands(10)}},
    )

    with pytest.raises(ProviderFailure, match="has_more=<absent>"):
        _provider(transport).fetch_panel(_request())


def test_a_page_longer_than_the_limit_it_asked_for_is_refused() -> None:
    """`limit` was measured to narrow only. A longer page means the endpoint stopped honouring
    it, so the offsets this loop computes no longer line up with the rows it is served -- which
    would silently skip whatever fell between them."""
    transport = StaticTransport(
        _response(_bands(TUSHARE_STK_LIMIT_ROW_CAP), has_more=True),
        _response(_bands(5000), has_more=False),
    )

    with pytest.raises(ProviderFailure, match="asked for 4000 rows and was served 5000"):
        _provider(transport).fetch_panel(_request())


def test_the_evidence_plane_gets_the_same_fallback() -> None:
    """`stk_limit` serves both planes, and a cap is a property of the response rather than of
    the plane that asked for it. Putting the fallback in `_request_rows` -- above both decoders
    -- is what makes `fetch()`, `fetch_panel()`, the REST face and the SDK share it instead of
    the CLI having its own."""
    transport = PagingTransport(_bands(7900))

    batch = _provider(transport).fetch(_request())

    assert batch.status == "success"
    assert len(batch.records) == 7900


# --- the retry policy -----------------------------------------------------------------------


class FlakyTransport:
    """Raises the given exceptions in order, then answers."""

    def __init__(self, *failures: Exception, rows: int = 3) -> None:
        self.failures = list(failures)
        self.rows = rows
        self.calls = 0

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= len(self.failures):
            raise self.failures[self.calls - 1]
        return _response(_bands(self.rows), has_more=False)


def _recording_sleep() -> tuple[list[float], Any]:
    slept: list[float] = []
    return slept, slept.append


def test_a_transient_transport_error_is_retried_rather_than_voiding_the_fetch() -> None:
    """The measured failure: a build 27 seconds in met one transient error and the whole round
    was voided with `invalid_response`. Two things had to change together -- the transport
    exception is now translated into `upstream`/retryable where it happens, and the flag now
    decides something."""
    slept, sleep = _recording_sleep()
    transport = FlakyTransport(TimeoutError("read timed out"), ConnectionResetError())

    batch = _provider(transport, sleep=sleep, jitter=lambda: 0.5).fetch_panel(_request())

    assert batch.status == "success"
    assert transport.calls == 3
    assert slept == [1.0, 2.0]


def test_the_retry_is_bounded_and_the_last_failure_is_the_one_that_reaches_the_caller() -> None:
    """Bounded, because a `panel build` is ~720 whole-market requests: an unbounded retry turns
    a real outage into a job that never ends and never reports. The category that escapes is
    `upstream` with `retryable=True`, which is the truth about a network error -- not the
    `invalid_response`/`retryable=False` it used to be."""
    slept, sleep = _recording_sleep()
    transport = FlakyTransport(*[TimeoutError("read timed out")] * 10)

    with pytest.raises(ProviderFailure) as raised:
        _provider(transport, sleep=sleep, jitter=lambda: 0.5).fetch_panel(_request())

    assert transport.calls == TUSHARE_TRANSPORT_ATTEMPTS
    assert len(slept) == TUSHARE_TRANSPORT_ATTEMPTS - 1
    assert raised.value.category == "upstream"
    assert raised.value.retryable is True


def test_a_rate_limited_response_is_named_as_one_and_waits_for_the_window() -> None:
    """`rate_limit` is a declared category this provider had never once produced. Measured
    2026-08-10: 100 of 600 concurrent `income` requests answered `code=40203` naming a
    500-per-minute interface quota, over HTTP 200 -- and every one of them was classified
    `upstream`. The delay is the quota's window rather than the exponential curve, because 1s +
    2s + 4s spends the whole retry budget inside the minute that is refusing."""
    slept, sleep = _recording_sleep()
    limited = {"code": TUSHARE_RATE_LIMIT_CODE, "msg": "frequency exceeded (500/min)"}
    transport = StaticTransport(limited)

    with pytest.raises(ProviderFailure) as raised:
        _provider(transport, sleep=sleep, jitter=lambda: 0.5).fetch_panel(_request())

    assert raised.value.category == "rate_limit"
    assert raised.value.retryable is True
    assert slept == [TUSHARE_RATE_LIMIT_DELAY] * (TUSHARE_TRANSPORT_ATTEMPTS - 1)


def test_a_rejected_credential_is_not_retried() -> None:
    """Measured 2026-08-10: a wrong token answers `code=40101`, which the original mapping
    classified `upstream`/retryable because the only code it knew was `-2001`. So the single
    most common real failure was both mis-named and advertised as worth trying again -- and a
    client that keeps resending a rejected credential turns a typo into a hammering loop."""
    slept, sleep = _recording_sleep()
    transport = StaticTransport({"code": 40101, "msg": "the token is not right"})

    with pytest.raises(ProviderFailure) as raised:
        _provider(transport, sleep=sleep, jitter=lambda: 0.5).fetch_panel(_request())

    assert raised.value.category == "authentication"
    assert raised.value.retryable is False
    assert transport.payloads == transport.payloads[:1]
    assert slept == []


def test_the_backoff_is_jittered_and_capped() -> None:
    """Jitter matters even for a single process: a build is a tight loop of identical requests,
    so an unjittered backoff re-synchronises every retry onto the same instants as the failures
    that caused them. The cap is what keeps the fourth attempt from being minutes away."""
    slept, sleep = _recording_sleep()
    transport = FlakyTransport(*[TimeoutError()] * 10)

    with pytest.raises(ProviderFailure):
        _provider(transport, attempts=6, sleep=sleep, jitter=lambda: 0.0).fetch_panel(_request())

    assert slept == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_a_provider_that_makes_no_attempt_at_all_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="fewer than one attempt"):
        TushareProvider(token="secret-token", transport=StaticTransport({}), attempts=0)


# --- the probe hooks ------------------------------------------------------------------------


def test_every_declared_dataset_has_a_probe_request_its_own_builder_accepts() -> None:
    """The provider-side half of R12. Nine of fifteen datasets sent no request at all, five of
    them because their `params_builder` refused the empty subject tuple the probe supplied --
    and the resulting `configuration` was the same word a missing `TUSHARE_TOKEN` produces.

    Asserted by driving the *real* builders rather than by comparing the hook's output against
    a list: a subject tuple that satisfied this test and not `_index_weight_params` would prove
    nothing, and that is exactly the failure mode the original probe had.
    """
    provider = TushareProvider(token="secret-token", clock=lambda: FETCHED_AT)

    for descriptor in TUSHARE_DATASETS:
        request = ProviderRequest(
            dataset=descriptor.dataset,
            as_of=FETCHED_AT,
            subjects=provider.probe_subjects(descriptor.dataset),
        )
        params = descriptor.params_builder(request)
        assert params, f"{descriptor.dataset} built an empty params object"


def test_the_panel_only_datasets_are_probed_on_the_panel_plane() -> None:
    """The four that `fetch()` refuses *by design*, and the reason the probe reported
    `configuration` for them in every environment regardless of the credential. Read off
    `serves_evidence_plane` rather than listed by name, so a future panel-only descriptor is
    probed on the right plane the day it is added."""
    provider = TushareProvider(token="secret-token", clock=lambda: FETCHED_AT)
    panel_only = {entry.dataset for entry in TUSHARE_DATASETS if not entry.serves_evidence_plane}

    assert panel_only == {"stock_basic", "namechange", "index_classify", "index_member_all"}
    for descriptor in TUSHARE_DATASETS:
        expected = "panel" if descriptor.dataset in panel_only else "evidence"
        assert provider.probe_plane(descriptor.dataset) == expected


def test_the_financial_indicator_probe_names_a_report_period_year_from_the_clock() -> None:
    """`_financial_indicator_params` refuses anything but a four-digit period year, and it must
    not be a literal: a pinned year would have to be revisited every January for no gain, since
    a probe cares that the request was *accepted* and `no_data` is an accepted request."""
    provider = TushareProvider(token="secret-token", clock=lambda: FETCHED_AT)

    assert provider.probe_subjects("fina_indicator") == ("000001.SZ", "2025")


# --- the disclosure and the descriptor -------------------------------------------------------


def test_the_published_limitation_states_the_headroom_at_the_resolution_that_decides_it() -> None:
    """`KNOWN_SUSPENSION_LIMITATIONS` is what `panel doctor --json` hands a reader, so the
    numbers in it are the ones an operator plans against -- and it said the cap was "inside a
    year" away when the measured answer is about thirty sessions. A yearly rate is the wrong
    resolution for a 66-row margin: it is right to within a factor of eight, and eight times
    six weeks is the difference between "schedule the fix" and "the build stopped working".

    Pinned as text because the entry *is* the deliverable -- `domain/` cannot import
    `providers/` (ADR-0003), so the disclosure and the descriptor can only be held together
    from a test, and this is that test. The per-session rate, the session count and the two
    subject-universe counts are each asserted, because an entry that named the conclusion
    without the measurement behind it would be back where it started.
    """
    entry = next(
        item
        for item in KNOWN_SUSPENSION_LIMITATIONS
        if item.code == "silent_truncation_at_a_cap_this_cross_section_is_close_to"
    )

    assert "+2.231 rows per session" in entry.detail
    assert "29.6 sessions" in entry.detail
    assert "7,734" in entry.detail  # the 2026-08-10 cross section the margin is measured from
    assert "5,878" in entry.detail and "2,194" in entry.detail  # why subjects cannot shard it
    assert "page_size" in entry.detail


def test_the_disclosure_and_the_descriptor_agree_about_the_cap_and_the_page_size() -> None:
    """The failure Task 38 booked, in its general form: a table and an implementation that
    drifted apart, with nothing that could see it. Here the table is prose in `domain/` and the
    implementation is a descriptor in `providers/`, so nothing but an assertion can hold them
    together -- and a limitations entry naming a cap the provider no longer enforces would read
    as a live disclosure while enforcing nothing."""
    entry = next(
        item
        for item in KNOWN_SUSPENSION_LIMITATIONS
        if item.code == "silent_truncation_at_a_cap_this_cross_section_is_close_to"
    )
    descriptor = _descriptor(PRICE_LIMIT_DATASET)
    assert descriptor.page_size is not None

    assert f"{descriptor.max_rows_per_response:,}" in entry.detail
    assert f"{descriptor.page_size:,}" in entry.detail
    assert f"{descriptor.max_rows_per_response - MEASURED_CROSS_SECTION - 1}" in entry.detail


# --- a session that has not published yet -----------------------------------------------------


def test_a_halt_announced_for_a_session_that_has_not_published_is_not_a_decode_error() -> None:
    """Found by running `doctor --probe` against the live endpoint at 05:29 Asia/Shanghai.

    `suspend_d(trade_date=20260811)` served two rows at that instant -- a halt announced for a
    session whose 16:30 availability had not arrived. `_daily_close_timeline` stamped
    `ingested_time` at the fetch instant, `Timeline` forbids `available_time > ingested_time`,
    and the resulting `ValueError` surfaced one frame up as
    `ProviderFailure(invalid_response, "Tushare response could not be decoded: ...")` -- a
    decode error about a response that decoded perfectly, reported every morning before the
    close for an endpoint that was working.

    Two assertions, and the second is the one that keeps the repair honest: the fetch succeeds,
    **and** the not-yet-knowable row is discarded rather than stored with an overstated clock.
    """
    before_the_close = datetime(2026, 8, 10, 21, 30, tzinfo=UTC)  # 05:30 Asia/Shanghai, 08-11
    published = ["20260807", "600000.SH", 12.03, 9.85]
    not_yet = ["20260811", "600001.SH", 12.03, 9.85]
    transport = StaticTransport(_response([published, not_yet], has_more=False))
    provider = TushareProvider(
        token="secret-token", transport=transport, clock=lambda: before_the_close
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=PRICE_LIMIT_DATASET, as_of=before_the_close)
    )

    assert batch.status == "success"
    assert batch.subjects == ("600000.SH",)
    assert max(batch.timeline.available_time) <= before_the_close


def test_a_builder_that_already_sets_limit_or_offset_cannot_be_paged() -> None:
    """A guard for a condition no descriptor in this table produces today, and the reason it is
    a refusal rather than a comment is what would happen if one ever did.

    The `page_size` fallback computes `offset = index * page_size` over the *whole* answer. A
    builder that already carried an `offset` would compose the two, so the pages would no longer
    be a partition of the response -- and the property everything downstream rests on ("the
    concatenation is what a single response would have served, in order") would be false with
    nothing raising. Driven directly because that is the only way to reach it: `_paged_params`
    is unreachable through any descriptor this table declares, which is exactly why an
    unasserted guard here would rot.
    """
    with pytest.raises(ProviderFailure, match="already sets 'offset'"):
        _paged_params({"trade_date": "20260807", "offset": "500"}, limit=4000, offset=0)

    with pytest.raises(ProviderFailure, match="already sets 'limit'"):
        _paged_params({"trade_date": "20260807", "limit": "10"}, limit=4000, offset=0)

    assert _paged_params({"trade_date": "20260807"}, limit=4000, offset=8000) == {
        "trade_date": "20260807",
        "limit": "4000",
        "offset": "8000",
    }
