from datetime import datetime, timezone

from app.descriptions.writer import PageForDescription, usable
from app.models.reasoning import TitleReasoningCandidate, TitleReasoningInput
from app.models.snapshots import SnapshotMetadata
from app.reasoning.claims import promises_not_made
from app.reasoning.validator import validate_title_candidate


def test_a_promise_the_page_never_made_is_found_however_it_is_spelled():
    assert promises_not_made("مرکز ایمپلنت دندان در تهران با تخفیف") == ["تخفیف"]
    assert promises_not_made("ایمپلنت دندان + مشاوره رايگان") == ["رایگان"]  # Arabic yeh
    assert promises_not_made("کفش رانینگ | ارسال‌فوری") == ["ارسالفوری"]  # half-space
    assert "بهترین" in promises_not_made("بهترین کلینیک ایمپلنت")
    assert "رزرو" in promises_not_made("برای رزرو نوبت کلیک کنید")


def test_marks_that_do_not_change_the_word_do_not_hide_it():
    assert promises_not_made("تَخفیف ویژه کفش") == ["تخفیف"]  # a diacritic
    assert promises_not_made("تخ\u200bفیف کفش") == ["تخفیف"]  # a zero-width space
    assert promises_not_made("تخفـیف کفش") == ["تخفیف"]  # a stretched letter
    assert promises_not_made("فروشگاه شماره 1 کفش") == ["شماره۱"]  # Latin digit
    assert promises_not_made("Black Friday sale") == ["sale"]


def test_everyday_persian_that_only_looks_like_a_promise_is_left_alone():
    assert promises_not_made("دو نوبت در روز مصرف شود") == []
    assert promises_not_made("پلاک شماره ۱۵ خیابان آزادی") == []
    assert promises_not_made("کافه با coffee تازه و خدمات کترینگ") == []
    assert promises_not_made("استخدام freelance طراح") == []
    assert promises_not_made("ارزنده‌ترین تجربه‌ها") == []
    assert promises_not_made("در صدد خرید خانه؟ راهنمای کامل") == []
    assert promises_not_made("کفش با ۲۰ درصد تخفیف") == ["تخفیف", "درصد"]


def test_a_promise_the_page_makes_itself_may_stay():
    assert promises_not_made("کفش رانینگ با ارسال رایگان", "کفش رانینگ | ارسال رایگان") == []
    assert promises_not_made("آموزش پایتون از صفر", "آموزش پایتون") == []


def title_input(current="ایمپلنت دندان در تهران", competitors=("ایمپلنت دندان با تخفیف ویژه",)):
    return TitleReasoningInput(
        recommendation_id="r", site_id="s", normalized_url="https://clinic.example/implant",
        primary_query="ایمپلنت دندان", current_title=current, target_position=9,
        competitor_titles=list(competitors), competitor_title_length_median=None,
        competitor_title_length_average=None, title_length_gap_vs_competitors=None, confidence_score=1.0,
        constraints=[], evidence={},
        snapshot=SnapshotMetadata(snapshot_id="x", data_snapshot_id="x", rule_version="x", config_version="x",
                                  generated_at=datetime.now(timezone.utc)),
    )


def candidate(title):
    return TitleReasoningCandidate(candidate_id="c", title=title, rationale="r", confidence_score=0.9)


def test_a_title_promising_what_only_a_competitor_does_is_rejected():
    errors = validate_title_candidate(candidate=candidate("ایمپلنت دندان در تهران با تخفیف"), reasoning_input=title_input())
    assert "title_makes_a_promise_the_page_does_not" in errors
    assert validate_title_candidate(candidate=candidate("ایمپلنت دندان در تهران | کلینیک تخصصی"), reasoning_input=title_input()) == []


def test_a_description_making_a_promise_is_left_out_and_a_plain_one_kept():
    page = PageForDescription(url="https://clinic.example/implant", title="ایمپلنت دندان | کلینیک نمونه",
                              h1="ایمپلنت دندان با متخصص پریودنتیست", current=None, keyword="ایمپلنت دندان")
    plain = "ایمپلنت دندان با متخصص پریودنتیست در کلینیک نمونه؛ برای آشنایی با این خدمت و مراحل آن، این صفحه را ببینید."
    promising = "ایمپلنت دندان با متخصص پریودنتیست در کلینیک نمونه، همراه با مشاوره رایگان؛ همین حالا نوبت بگیرید."
    raw = '{"descriptions": ["%s", "%s"]}' % (promising, plain)
    assert usable(raw, page) == [plain]
    assert usable(raw) == [promising, plain]  # without the page, nothing to hold it to
