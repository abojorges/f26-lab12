"""Validation, statement assembly, batch pricing, and rendering."""

from dataclasses import replace
from datetime import datetime

import pytest

from refunds import service
from refunds.models import TimeInterval, utc

WELL_AHEAD = utc(2026, 3, 1, 14, 0)


def reject(booking, request):
    with pytest.raises(service.RefundRequestError):
        service.validate_request(booking, request)


def test_a_matching_request_validates(booking, cancellation):
    service.validate_request(booking, cancellation(WELL_AHEAD))


def test_validation_rejects_what_the_policy_cannot_price(booking, cancellation):
    backwards = TimeInterval(booking.interval.end, booking.interval.start)
    reject(booking, cancellation(WELL_AHEAD, booking_id="bk-9999"))
    reject(booking, cancellation(datetime(2026, 3, 1, 14, 0)))
    reject(booking, cancellation(utc(2026, 1, 5, 9, 0)))
    reject(booking, cancellation(WELL_AHEAD, requested_by=""))
    reject(replace(booking, price_as_booked_cents=-1), cancellation(WELL_AHEAD))
    reject(replace(booking, interval=backwards), cancellation(WELL_AHEAD))


def test_a_full_statement_withholds_nothing(booking):
    statement = service.build_statement(booking, 12000, "full", adjustments=[])
    assert statement.is_full_refund
    assert statement.withheld_cents == 0
    assert statement.adjustments == ("settled under the full tier",)


def test_a_partial_statement_records_what_was_withheld(booking):
    statement = service.build_statement(booking, 9000, "standard", adjustments=["batch 2026-03"])
    assert statement.withheld_cents == 3000
    assert statement.adjustments[0] == "batch 2026-03"
    assert "withheld $30.00 of $120.00" in statement.adjustments[-1]


def test_a_decision_carries_the_room_the_department_and_the_reason(booking, cancellation):
    request = cancellation(utc(2026, 3, 7, 14, 0), reason="room swap")
    statement = service.decide_refund(booking, request)
    assert statement.refund_cents == 9000
    assert statement.tier_name == "standard"
    assert statement.adjustments[0] == "room Gates 4301, Statistics"
    assert statement.adjustments[1] == "reason on file: room swap"


@pytest.mark.parametrize(
    "when",
    [utc(2026, 3, 10, 14, 0), utc(2026, 3, 10, 14, 30)],
    ids=["at-the-start", "half-an-hour-in"],
)
def test_a_cancellation_once_the_booking_has_started_settles_at_zero(booking, cancellation, when):
    # policy.refund_cents: a booking that has already started is a no-show and settles
    # at zero. Intervals are half-open, so the start instant itself counts as started.
    statement = service.decide_refund(booking, cancellation(when))
    assert statement.refund_cents == 0
    assert statement.withheld_cents == 12000
    assert statement.tier_name == "none"


def test_a_batch_prices_every_cancellation_it_names(booking, cancellation):
    other = replace(booking, booking_id="bk-2292", price_as_booked_cents=4000)
    requests = [
        cancellation(WELL_AHEAD),
        cancellation(utc(2026, 3, 7, 14, 0), booking_id="bk-2292"),
    ]
    statements = service.decide_batch([booking, other], requests)
    assert [statement.refund_cents for statement in statements] == [12000, 3000]
    assert service.total_refunded_cents(statements) == 15000
    assert "batch total: $150.00" in service.render_batch(statements)


def test_a_batch_rejects_a_cancellation_with_no_booking_on_file(booking, cancellation):
    with pytest.raises(service.RefundRequestError):
        service.decide_batch([booking], [cancellation(WELL_AHEAD, booking_id="bk-0")])


def test_rendering_lists_the_money_the_tier_and_the_notes(booking, cancellation):
    statement = service.decide_refund(booking, cancellation(WELL_AHEAD))
    text = service.render_statement(statement)
    assert "price as booked: $120.00" in text
    assert "refund: $120.00" in text
    assert "tier: full" in text
    assert "note: room Gates 4301, Statistics" in text
    assert service.activity_line(statement) == "bk-2291: $120.00 back of $120.00 (full)"
