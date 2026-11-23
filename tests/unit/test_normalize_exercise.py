"""`normalize/exercise.py` 的纯函数用例（`KA-53` 的切分判据）。

题目边界只由规则给：题号闭集＋同父单调序。这里把五种题号形状、半角折叠、回退视为正文、
页区间与切片边界逐条钉住——真机读数只在批次流水里补，判据先在这里先红后绿。
"""

from __future__ import annotations

from axiom_flow.normalize.exercise import (
    ExerciseItem,
    exercise_item_key,
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

    def test_ascii_square_bracket_shape(self) -> None:
        """模型把 prompt 里的 `[N]` 原样抄回（`KA-56` 要求题号照抄）：ASCII 方括号与 `【N】` 同键。"""
        assert item_number("[7]") == "7"
        assert item_number("[2-10]") == "2-10"

    def test_four_digit_numbers(self) -> None:
        """b07／b11 全书题号到四位数（真机末题 4462）：三位数封顶会把章尾整段题切成正文。"""
        assert item_number("4457. 求极限") == "4457"
        assert item_number("【4462】证明") == "4462"

    def test_english_problem_shape(self) -> None:
        """b17 解答册的题号是 `Problem N.M:`：题号取 N.M，`Problem` 不是名字的一部分。"""
        assert item_number("Problem 1.1: If $r \\in \\mathbf{Q}$") == "1-1"
        assert item_number("Problem 12.13: Prove") == "12-13"
        assert item_number("Solution: We prove this") is None
        assert item_number("Alternate solution (Sketch): If $x$") is None


class TestExerciseItemKey:
    """模型回的题号 → 比对键（配对机械失败，REQ-178）：包装、全角与尾标点都要折到同一把键。

    b07 的 662 笔审核失败里 472 笔是模型把 `[N]` 原样带回、98 笔是 `（N）`——两侧必须同一把键
    才谈得上命中；原样文本折出的键不许把 `第 6 题` 这类正文冒充成题号。
    """

    def test_wrapped_reply_numbers_fold_to_the_same_key(self) -> None:
        assert exercise_item_key("[6]") == "6"
        assert exercise_item_key("【6】") == "6"
        assert exercise_item_key("（6）") == "6"
        assert exercise_item_key("(6)") == "6"
        assert exercise_item_key("６") == "6"
        assert exercise_item_key("6.") == "6"
        assert exercise_item_key("6、") == "6"
        assert exercise_item_key(" 6 ") == "6"

    def test_dash_and_dot_keys_fold_together(self) -> None:
        assert exercise_item_key("2-10") == "2-10"
        assert exercise_item_key("[2-10]") == "2-10"
        assert exercise_item_key("2.10") == "2-10"

    def test_placeholder_text_is_not_a_key(self) -> None:
        assert exercise_item_key("题号原文") is None
        assert exercise_item_key("第 6 题") is None
        assert exercise_item_key("") is None


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

    def test_bracket_family_differs_from_paren_body_enumeration(self) -> None:
        """REQ-179 R3（b07 真机取证）：`【N】` 题与正文枚举 `(N)` 是**不同形状族**。

        b07 用 `【N】` 排题号、教材正文里的小问用 `(N)`——旧判定把两种括号归同一族，
        正文里的 `(2)`（下确界定义的枚举小问）就能通过「同族＋单调」闸收成假题，
        吞掉其后直到下一枚真题号的整段教材正文（`afkp-76774787c71d` 的串位形态）。
        分族后 `(N)` 与 `【N】` 异族 → 按正文回退，串位消除；裸 `(N)` 自成节的排法
        （`test_bracketed_section_keeps_its_own_family`）不受影响。
        """
        text = "\n".join(
            [
                "下确界的定义如下：",
                "(1) 每一个 $x \\in X$ 满足不等式；",
                "(2) 对于任何的 $\\varepsilon > 0$，存在 $x'$。",
                "同样，上确界定义为：",
                "【2】设 $A$ 非空，证明……",
                "【3】设 $B$ 有上界，证明……",
            ]
        )
        items = split_exercise_items([(10, text)])
        assert [item.item_no for item in items] == ["2", "3"]
        assert items[0].text.startswith("【2】"), "正文枚举 (1)/(2) 不进题面"
        assert "下确界" not in items[0].text

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

    def test_english_problem_shape_b17(self) -> None:
        """b17：`Problem N.M:` 起题，`Solution`／`Proof`／`Alternate solution` 全属解答正文。"""
        text = "\n".join(
            [
                "Problem 1.1: If $r \\in \\mathbf{Q}$.",
                "Solution: We prove this by contradiction.",
                "Problem 1.2: Prove that there is no $x$.",
                "Solution: Suppose there is such an $x$.",
                "Alternate solution (Sketch): divide by two.",
            ]
        )
        items = split_exercise_items([(1, text)])
        assert [item.item_no for item in items] == ["1-1", "1-2"]
        assert items[0].label == "Problem 1.1"
        assert "Alternate solution (Sketch)" in items[1].text

    def test_explicit_problem_family_outranks_a_stray_bracketed_line(self) -> None:
        """b17 章首屏带着上一章的尾巴：一枚 `(7)` 式公式编号不得锁死本节族。

        真机形状（p43）：章界取 `Problem 5.1` 首现页，页首先是上一章的公式编号行 `(7)`；
        弱形状先被接受会把族锁成括号、单调序水印抬到 7，随后整章的 `Problem 5.x:` 全被判成正文。
        只要本节出现显式英文题号，族就由它定，弱形状行一律算正文。
        """
        text = "\n".join(
            [
                "(7)",
                "Of these, the arrangement (7) gives an immediate contradiction.",
                "Problem 5.1: Let $f$ be a function such that",
                "Solution: We prove it.",
                "Problem 5.2: Find the limit.",
            ]
        )
        items = split_exercise_items([(43, text)])
        assert [item.item_no for item in items] == ["5-1", "5-2"]
        assert items[0].label == "Problem 5.1"
        assert "(7)" not in items[0].text

    def test_four_digit_item_numbers_split(self) -> None:
        items = split_exercise_items([(430, "4457.1. 小问。\n4458. 下一题。")])
        assert [item.item_no for item in items] == ["4457-1", "4458"]

    def test_problem_shape_needs_its_colon(self) -> None:
        """行首的 `Problem N.M` 没有冒号＝正文里的交叉引用，不是题目标题（b17 p98 的 8.26）。"""
        text = "\n".join(
            [
                "Problem 8.26 will imply that the series diverges.",
                "That is all we need here.",
            ]
        )
        assert split_exercise_items([(98, text)]) == ()

    def test_problem_heading_may_lead_a_parenthetical(self) -> None:
        """b17 的三道题标题带括号备注（6.11／6.12／7.22）：`Problem N.M (…)` 仍是题目。"""
        text = "\n".join(
            [
                "Problem 6.11 (with $\varepsilon$ fixed): Show that",
                "Solution: By compactness.",
            ]
        )
        items = split_exercise_items([(1, text)])
        assert [item.item_no for item in items] == ["6-11"]
        assert items[0].label == "Problem 6.11"

    def test_problem_family_tolerates_out_of_order_numbers(self) -> None:
        """b17 第 3 章把 3.21／3.22 排在小节前：题号是显式标题，节内乱序照样成题。

        教材册的答案段靠严格递增切分（另一把尺），这道宽容只给 `Problem N.M` 族：
        每枚题号都是排版出来的题目标题，出现顺序不代表题号顺序。
        """
        text = "\n".join(
            [
                "Problem 3.1: First.",
                "Solution: a.",
                "Problem 3.2: Second.",
                "Problem 3.21: Third.",
                "Problem 3.22: Fourth.",
                "Problem 3.6: Fifth.",
                "Problem 3.7: Sixth.",
                "Problem 3.1: A reference back to the first.",
            ]
        )
        items = split_exercise_items([(1, text)])
        assert [item.item_no for item in items] == ["3-1", "3-2", "3-21", "3-22", "3-6", "3-7"]
        assert items[4].text.startswith("Problem 3.6")
        assert "A reference back" in items[-1].text

    def test_paren_family_stays_strict(self) -> None:
        """乱序宽容只给显式英文题号族：括号族的回退仍按「宁可少切」当正文。"""
        text = "\n".join(["(1) 第一问。", "(5) 跳号。", "(2) 回退。", "(6) 再接。"] )
        items = split_exercise_items([(1, text)])
        assert [item.item_no for item in items] == ["1", "5", "6"]
