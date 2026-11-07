"""`normalize/exercise.py` 的纯函数用例（`KA-53` 的切分判据）。

题目边界只由规则给：题号闭集＋同父单调序。这里把五种题号形状、半角折叠、回退视为正文、
页区间与切片边界逐条钉住——真机读数只在批次流水里补，判据先在这里先红后绿。
"""

from __future__ import annotations

from axiom_flow.normalize.exercise import (
    ExerciseItem,
    item_number,
    split_exercise_items,
)


class TestItemNumber:
    def test_closed_set_five_shapes(self) -> None:
        assert item_number("1. 求极限") == "1"
        assert item_number("12、证明下列等式") == "12"
        assert item_number("(3) 计算积分") == "3"
        assert item_number("【4】设函数") == "4"
        assert item_number("2-10 讨论连续性") == "2-10"

    def test_full_width_folds(self) -> None:
        assert item_number("（１）计算") == "1"
        assert item_number("３．求导") == "3"
        assert item_number("１２、证明") == "12"

    def test_non_item_lines_are_none(self) -> None:
        assert item_number("习题") is None
        assert item_number("1.2 极限的定义") is None
        assert item_number("第 3 题") is None
        assert item_number("1.2.3") is None
        assert item_number("") is None

    def test_dot_hierarchy_folds_to_dash(self) -> None:
        assert item_number("3.2. 求二阶导") == "3-2"


class TestSplitExerciseItems:
    def test_splits_on_monotonic_numbers(self) -> None:
        text = "\n".join(
            [
                "1. 求 $\\lim x^2$。",
                "解：略。",
                "2. 证明 $a^2+b^2\\ge 2ab$。",
                "证明：略。",
            ]
        )
        items = split_exercise_items([(10, text)])
        assert [item.item_no for item in items] == ["1", "2"]
        assert items[0].text == "1. 求 $\\lim x^2$。\n解：略。"
        assert items[1].text == "2. 证明 $a^2+b^2\\ge 2ab$。\n证明：略。"
        assert items[0].page_start == 10 and items[0].page_end == 10

    def test_regressed_numbers_stay_body_text(self) -> None:
        text = "\n".join(
            [
                "1. 主问题。",
                "(1) 小问一。",
                "(2) 小问二。",
                "2. 第二题。",
            ]
        )
        items = split_exercise_items([(5, text)])
        assert [item.item_no for item in items] == ["1", "2"]
        assert "(1) 小问一。" in items[0].text
        assert "(2) 小问二。" in items[0].text

    def test_bracketed_section_keeps_its_own_family(self) -> None:
        text = "(1) 第一题。\n解答。\n(2) 第二题。"
        items = split_exercise_items([(5, text)])
        assert [item.item_no for item in items] == ["1", "2"]

    def test_reset_is_per_call(self) -> None:
        first = split_exercise_items([(1, "1. 甲\n2. 乙")])
        second = split_exercise_items([(9, "1. 丙")])
        assert [item.item_no for item in first] == ["1", "2"]
        assert [item.item_no for item in second] == ["1"]

    def test_page_span_covers_lines(self) -> None:
        items = split_exercise_items(
            [
                (20, "1. 第一问。\n续行。"),
                (21, "继续。\n2. 第二问。"),
            ]
        )
        assert items[0].page_start == 20 and items[0].page_end == 21
        assert items[1].page_start == 21 and items[1].page_end == 21
        assert "续行。" in items[0].text and "继续。" in items[0].text

    def test_preamble_is_dropped(self) -> None:
        items = split_exercise_items([(1, "本章习题如下：\n1. 第一题。")])
        assert len(items) == 1
        assert items[0].text.startswith("1.")

    def test_no_numbers_means_zero_items(self) -> None:
        assert split_exercise_items([(1, "没有题号的正文。\n还是正文。")]) == ()

    def test_blank_pages_yield_zero_items(self) -> None:
        assert split_exercise_items([(1, ""), (2, "   ")]) == ()

    def test_bracket_shape_b07(self) -> None:
        items = split_exercise_items([(30, "【7】设 $f$ 连续。\n【8】求 $f'$。")])
        assert [item.item_no for item in items] == ["7", "8"]
        assert isinstance(items[0], ExerciseItem)
        assert items[0].label == "【7】"
