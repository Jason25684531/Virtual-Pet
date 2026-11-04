from demo_scripts import match


def test_home_and_elden_scripts_choose_the_expected_language():
    home = match("Any cute home decor you'd recommend?")
    assert home is not None
    assert home[0].name == "home_decor"
    assert home[1] == home[0].answer_en

    home_zh = match("推薦居家擺設")
    assert home_zh is not None
    assert home_zh[1] == home_zh[0].answer_zh

    elden = match("艾爾登法環很久沒玩了，帶我複習基礎")
    assert elden is not None
    assert elden[0].name == "elden_ring"
    assert elden[1] == elden[0].answer_zh


def test_unrelated_questions_do_not_match():
    assert match("What should I have for lunch?") is None


def test_matching_ignores_case_and_punctuation_but_requires_all_keyword_groups():
    assert match("ANY CUTE HOME DECOR, I'D RECOMMEND!") is not None
    assert match("艾爾登法環？怎麼玩！") is not None
    assert match("elden ring") is None
