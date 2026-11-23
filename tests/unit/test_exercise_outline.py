"""习题集确定性目录与答案回配的纯函数用例（PLAN-036 W9~W11）。

三本真机形状：b11 目录页含「部分」行须按章优先剔掉、答案段由运行页眉 `答案` 连续成窗；
b17 无目录、章号由 `Problem N.M` 首段派生；b11 答案段是行内连排，只按**严格递增**题号切分。
这里只钉纯函数判据，批次读数在 W12 真机取证里补。
"""

from __future__ import annotations

from axiom_flow.normalize.exercise import homework_chapters, textbook_answer_key
from axiom_flow.normalize.toc import (
    answer_section_window,
    chapter_number,
    exercise_chapter_hints,
)
from axiom_flow.schemas import Block, BlocksPage, BlockType

IMAGE_SIZE = (1240, 1754)


def _page(page_no: int, *texts: str) -> BlocksPage:
    return BlocksPage(
        page=page_no,
        image_size=IMAGE_SIZE,
        source="mineru",
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                bbox=[72, 60 + index * 40, 1168, 100 + index * 40],
                index=index,
                order=index,
                text=text,
            )
            for index, text in enumerate(texts)
        ],
    )


B11_TOC = "\n".join(
    [
        "目录",
        "第一部分 一元函数 …… 1",
        "第一章 分析引论 …… 3",
        "第二章 一元函数微分学 …… 63",
        "第二部分 多元函数 …… 217",
        "第三章 不定积分 …… 116",
    ]
)


def _b11_pages() -> list[BlocksPage]:
    return [
        _page(9, B11_TOC),
        _page(15, "第一章 分析引论", "正文"),
        _page(75, "第二章 一元函数微分学", "正文"),
        _page(128, "第三章 不定积分", "正文"),
        _page(140, "答案", "16. 0; 1. 17. 2."),
        _page(141, "答案", "18. 3."),
    ]


class TestExerciseChapterHints:
    def test_chapter_rows_prefer_chapter_over_part(self) -> None:
        """b11 目录含「第一部分／第二部分」：有章行时部分行一条不进目录、也一条不报未定位。"""
        hints = exercise_chapter_hints(_b11_pages())
        assert [hint.name for hint in hints] == [
            "第一章 分析引论",
            "第二章 一元函数微分学",
            "第三章 不定积分",
        ]
        assert [hint.page_no for hint in hints] == [15, 75, 128]
        assert [hint.block_index for hint in hints] == [0, 0, 0]

    def test_parts_are_the_fallback_when_a_book_has_no_chapters(self) -> None:
        toc = "\n".join(
            ["目录", "第一篇 函数 …… 1", "第二篇 积分 …… 20", "第三篇 级数 …… 40"]
        )
        pages = [
            _page(5, toc),
            _page(9, "第一篇 函数", "正文"),
            _page(28, "第二篇 积分", "正文"),
            _page(48, "第三篇 级数", "正文"),
        ]
        assert [hint.name for hint in exercise_chapter_hints(pages)] == [
            "第一篇 函数",
            "第二篇 积分",
            "第三篇 级数",
        ]

    def test_unlocated_chapter_is_dropped_the_same_way_outline_drops_it(self) -> None:
        toc = "\n".join(
            [
                "目录",
                "第一章 甲 …… 3",
                "第二章 乙 …… 63",
                "第三章 丙 …… 116",
                "第四章 丁 …… 140",
            ]
        )
        pages = [
            _page(9, toc),
            _page(15, "第一章 甲", "正文"),
            _page(75, "第二章 乙", "正文"),
            _page(128, "第三章 丙", "正文"),
        ]
        assert [hint.name for hint in exercise_chapter_hints(pages)] == [
            "第一章 甲",
            "第二章 乙",
            "第三章 丙",
        ]

    def test_no_toc_page_means_no_chapter_hints(self) -> None:
        assert exercise_chapter_hints([_page(1, "Problem 1.1: a"), _page(2, "正文")]) == ()


class TestAnswerSectionWindow:
    def test_contiguous_running_header_is_the_window(self) -> None:
        assert answer_section_window(_b11_pages()) == (140, 141, 0)

    def test_a_page_without_the_header_ends_the_window(self) -> None:
        pages = _b11_pages() + [_page(142, "19. 4.")]
        assert answer_section_window(pages) == (140, 141, 0)

    def test_no_header_means_none(self) -> None:
        assert answer_section_window([_page(9, B11_TOC), _page(15, "第一章 甲", "正文")]) is None

    def test_the_word_inside_a_sentence_is_not_a_header(self) -> None:
        pages = [_page(9, B11_TOC), _page(140, "下面给出答案与提示。")]
        assert answer_section_window(pages) is None

    def test_a_toc_entry_named_answer_is_not_a_header(self) -> None:
        toc = "\n".join(["目录", "第一章 甲 …… 3", "第二章 乙 …… 63", "答案 …… 100"])
        pages = [_page(9, toc), _page(15, "第一章 甲", "正文"), _page(75, "第二章 乙", "正文")]
        assert answer_section_window(pages) is None


class TestHomeworkChapters:
    def test_first_component_is_the_chapter_and_first_page_wins(self) -> None:
        pages = [
            (1, "Problem 1.1: a\nSolution: b"),
            (2, "Problem 1.3: c"),
            (7, "Problem 2.1: d"),
            (14, "Problem 2.4: e"),
            (31, "Problem 3.2: f"),
        ]
        assert homework_chapters(pages) == ((1, 1), (2, 7), (3, 31))

    def test_answer_words_are_not_chapter_sources(self) -> None:
        pages = [(1, "Solution (sketch): nothing here\nProof: nothing either")]
        assert homework_chapters(pages) == ()


class TestTextbookAnswerKey:
    def test_inline_answers_split_on_increasing_numbers(self) -> None:
        pages = [
            (
                140,
                "答案\n第一章\n16. 0; 1. 17. $-\\sqrt{2}$; $\\sqrt{2}$.\n22. -1.01 < x < -0.99.",
            )
        ]
        entries = textbook_answer_key(pages)
        assert [(entry.item_no, entry.text) for entry in entries] == [
            ("16", "0; 1."),
            ("17", "$-\\sqrt{2}$; $\\sqrt{2}$."),
            ("22", "-1.01 < x < -0.99."),
        ]

    def test_regressing_numbers_stay_inside_the_previous_answer(self) -> None:
        assert [(entry.item_no, entry.text) for entry in textbook_answer_key([(1, "10. 5. 11. 6.")])] == [
            ("10", "5."),
            ("11", "6."),
        ]

    def test_chapter_headings_do_not_become_answer_text(self) -> None:
        pages = [(1, "第一章\n16. 0; 1. 17. X\n第二章\n90. Y")]
        assert [(entry.item_no, entry.text) for entry in textbook_answer_key(pages)] == [
            ("16", "0; 1."),
            ("17", "X"),
            ("90", "Y"),
        ]

    def test_four_digit_numbers_and_sub_items_keep_their_shape(self) -> None:
        entries = textbook_answer_key([(400, "4457.1. 甲 4458. 乙")])
        assert [entry.item_no for entry in entries] == ["4457-1", "4458"]
        assert [entry.text for entry in entries] == ["甲", "乙"]

    def test_decimal_values_are_not_item_numbers(self) -> None:
        """`0.41%`／`1.01` 这类小数值不能冒充题号：题号后必须不是数字。"""
        assert textbook_answer_key([(1, "9. 不超过 0.41%; 3.93 g/cm^3")])[0].item_no == "9"


class TestChapterNumber:
    def test_arabic_forms_and_full_width(self) -> None:
        assert chapter_number("第1章 实数系和复数系") == 1
        assert chapter_number("第 6 章 RIEMANN-STIELTJES 积分") == 6
        assert chapter_number("第１０章 微分形式的积分") == 10

    def test_chinese_numerals(self) -> None:
        assert chapter_number("第一章 分析引论") == 1
        assert chapter_number("第十一章 LEBESGUE 理论") == 11
        assert chapter_number("第二十章 甲") == 20

    def test_homework_names_carry_the_number(self) -> None:
        assert chapter_number("Homework 3") == 3

    def test_front_and_back_matter_have_no_number(self) -> None:
        assert chapter_number("前言") is None
        assert chapter_number("参考书目") is None
        assert chapter_number("习题 1.1") is None
