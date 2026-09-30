"""解析签名（`parser_signature`）单测：canonical 串、输入集白名单、哨兵与判定纯函数。

守护面（PLAN-019 W2）：
- **输入集＝冻结集合**：`ocr_engine` + `/health` 的 `service_version`/`protocol_version` +
  `block_policy_version`。键漂移即转红——多一个键会让「改一行超时配置」也判成代际变化，
  少一个键会让真正的解析代际变化静默不可见。
- **canonical 形式**：键按字典序的 JSON 规范化串 → SHA-256；同输入必同签名，四个输入项任一
  变化必改签名。
- **fail-closed**：模型版本读数缺失时不臆造签名（回算＝伪造第二事实源）。
- **判定纯函数**：`up_to_date` / `resume` / `need_reparse`（+ `signature_unknown` 代际），
  受理门（W3）与只读重跑清单（W4）共用同一份判据。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from axiom_flow.engines import mineru
from axiom_flow.normalize import blocks
from axiom_flow.orchestrator.signature import (
    SIGNATURE_INPUT_KEYS,
    UNKNOWN_SIGNATURE,
    SignatureInputs,
    VersionIdentity,
    block_policy_payload,
    block_policy_version,
    compute_signature,
    decide_rerun,
    signature_canonical,
    signature_payload,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _inputs(**overrides: object) -> SignatureInputs:
    payload = {
        "ocr_engine": "mineru",
        "service_version": "3.4.4",
        "protocol_version": 2,
        "block_policy_version": block_policy_version(),
    }
    payload.update(overrides)
    return SignatureInputs(**payload)  # type: ignore[arg-type]


class TestCanonicalForm:
    """canonical 串的形状与稳定性：签名是「同一事实只有一种写法」的产物。"""

    def test_input_keys_are_the_frozen_whitelist(self):
        payload = signature_payload(_inputs())

        assert tuple(sorted(payload)) == SIGNATURE_INPUT_KEYS
        assert SIGNATURE_INPUT_KEYS == (
            "block_policy_version",
            "ocr_engine",
            "protocol_version",
            "service_version",
        ), "签名输入集已冻结，增删键必须先在承载计划改判"

    def test_non_content_parameters_are_absent(self):
        payload = signature_payload(_inputs())

        for key in (
            "ocr_timeout",
            "parse_window_size",
            "processing_window_size",
            "chunk_policy_version",
            "backend",
            "status",
        ):
            assert key not in payload, f"{key} 只影响成败/进度/分块，不影响块内容，不得入签名"

    def test_signature_is_sha256_of_key_sorted_json(self):
        inputs = _inputs()

        canonical = signature_canonical(inputs)
        digest = compute_signature(inputs)

        assert list(json.loads(canonical)) == sorted(json.loads(canonical))
        assert digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert len(digest) == 64

    def test_identical_inputs_produce_identical_signature(self):
        assert compute_signature(_inputs()) == compute_signature(_inputs())

    @pytest.mark.parametrize(
        "field",
        ["ocr_engine", "service_version", "protocol_version", "block_policy_version"],
    )
    def test_each_input_alone_moves_the_signature(self, field: str):
        baseline = compute_signature(_inputs())
        overrides = {
            "ocr_engine": "paddleocr-vl",
            "service_version": "3.4.5",
            "protocol_version": 3,
            "block_policy_version": "f" * 64,
        }[field]

        assert compute_signature(_inputs(**{field: overrides})) != baseline, (
            f"{field} 变化必须改变签名，否则该代际对判定不可见"
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("ocr_engine", ""),
            ("service_version", ""),
            ("protocol_version", ""),
            ("block_policy_version", ""),
        ],
    )
    def test_missing_input_fails_closed(self, field: str, value: object):
        """探不到读数就不建签名：留空即伪造一个「未知代际」，会静默吞掉一次真代际变化。"""
        with pytest.raises(ValueError, match=field):
            _inputs(**{field: value})


class TestBlockPolicyVersion:
    """策略版本取代码级常量派生，不设配置位（配置写一套、代码算一套＝第二判据）。"""

    def test_payload_is_exactly_the_two_block_facing_constants(self):
        payload = block_policy_payload()

        assert tuple(sorted(payload)) == ("output_flags", "type_map")
        assert payload["type_map"] == {k: str(v) for k, v in blocks._TYPE_MAP.items()}
        assert payload["output_flags"] == dict(mineru._OUTPUT_FLAGS)

    def test_type_map_change_moves_the_policy_version(self, monkeypatch):
        baseline = block_policy_version()

        monkeypatch.setitem(blocks._TYPE_MAP, "ref_text_tmp", str(baseline))

        assert block_policy_version() != baseline, "改类型映射就是改块产物，签名必须随之翻转"

    def test_output_flags_change_moves_the_policy_version(self, monkeypatch):
        baseline = block_policy_version()

        monkeypatch.setitem(mineru._OUTPUT_FLAGS, "return_middle_json", "false")

        assert block_policy_version() != baseline, "产物形态开关缺失会让版面块恒缺，属代际变化"

    def test_chunking_policy_is_not_in_signature(self, monkeypatch):
        """分块规则不改 blocks 内容：混进签名会让改一行跳过集触发全本重跑判定。"""
        baseline = block_policy_version()

        monkeypatch.setattr(blocks, "_SKIP_IN_MARKDOWN", set())

        assert block_policy_version() == baseline


class TestRerunDecision:
    """判定纯函数：受理门与重跑清单共用，判据只在这里写一次。"""

    def test_no_active_version_is_never_parsed(self):
        decision = decide_rerun(active=None, current_source_sha256=SHA_A, current_signature="s" * 64)

        assert decision.verdict == "never_parsed"
        assert decision.job_id is None

    def test_matching_pair_is_up_to_date(self):
        active = VersionIdentity(job_id="j1", source_sha256=SHA_A, parser_signature="s" * 64)

        decision = decide_rerun(
            active=active, current_source_sha256=SHA_A, current_signature="s" * 64
        )

        assert decision.verdict == "up_to_date"
        assert decision.job_id == "j1", "409 要生效版本的 job_id，客户端据此读回已有产物"

    def test_unknown_generation_reports_signature_unknown_only(self):
        """"" 代际只判需重跑，不产出「参数已变」类结论（历史参数不可考，不得倒推）。"""
        active = VersionIdentity(
            job_id="j1", source_sha256=SHA_A, parser_signature=UNKNOWN_SIGNATURE
        )

        for current_source in (SHA_A, SHA_B):
            decision = decide_rerun(
                active=active, current_source_sha256=current_source, current_signature="s" * 64
            )
            assert decision.verdict == "need_reparse"
            assert decision.reason == "signature_unknown", (
                "未知代际与参数已变必须可分辨，否则清单首日读数会被当成「全量重跑指令」"
            )

    def test_source_change_is_need_reparse(self):
        active = VersionIdentity(job_id="j1", source_sha256=SHA_B, parser_signature="s" * 64)

        decision = decide_rerun(
            active=active, current_source_sha256=SHA_A, current_signature="s" * 64
        )

        assert (decision.verdict, decision.reason) == ("need_reparse", "source_changed")

    def test_signature_change_is_need_reparse(self):
        active = VersionIdentity(job_id="j1", source_sha256=SHA_A, parser_signature="c" * 64)

        decision = decide_rerun(
            active=active, current_source_sha256=SHA_A, current_signature="s" * 64
        )

        assert (decision.verdict, decision.reason) == ("need_reparse", "signature_changed")

    def test_in_flight_same_signature_resumes_instead_of_restarting(self):
        """在飞同签名 ⇒ 交回该任务（双跑只会白跑一遍，模型侧单占用已串行）。"""
        in_flight = VersionIdentity(job_id="j2", source_sha256=SHA_A, parser_signature="s" * 64)

        decision = decide_rerun(
            active=None,
            current_source_sha256=SHA_A,
            current_signature="s" * 64,
            in_flight=[in_flight],
        )

        assert decision.verdict == "resume"
        assert decision.job_id == "j2"

    def test_in_flight_same_signature_wins_over_outdated_active(self):
        active = VersionIdentity(job_id="j1", source_sha256=SHA_A, parser_signature="c" * 64)
        in_flight = VersionIdentity(job_id="j2", source_sha256=SHA_A, parser_signature="s" * 64)

        decision = decide_rerun(
            active=active,
            current_source_sha256=SHA_A,
            current_signature="s" * 64,
            in_flight=[in_flight],
        )

        assert (decision.verdict, decision.job_id) == ("resume", "j2")

    def test_up_to_date_wins_over_in_flight(self):
        """已有可用产物时不再指向在飞任务：清单口径是「这本书需不需要重跑」。"""
        active = VersionIdentity(job_id="j1", source_sha256=SHA_A, parser_signature="s" * 64)
        in_flight = VersionIdentity(job_id="j2", source_sha256=SHA_A, parser_signature="s" * 64)

        decision = decide_rerun(
            active=active,
            current_source_sha256=SHA_A,
            current_signature="s" * 64,
            in_flight=[in_flight],
        )

        assert (decision.verdict, decision.job_id) == ("up_to_date", "j1")

    def test_in_flight_other_signature_does_not_block(self):
        active = VersionIdentity(
            job_id="j1", source_sha256=SHA_A, parser_signature=UNKNOWN_SIGNATURE
        )
        in_flight = VersionIdentity(job_id="j2", source_sha256=SHA_A, parser_signature="c" * 64)

        decision = decide_rerun(
            active=active,
            current_source_sha256=SHA_A,
            current_signature="s" * 64,
            in_flight=[in_flight],
        )

        assert (decision.verdict, decision.reason) == ("need_reparse", "signature_unknown")

    def test_unknown_current_signature_fails_closed(self):
        """当前侧 `''` 不是代际而是「调用方没算签名」，与生效侧的 `''` 语义正交。"""
        active = VersionIdentity(job_id="j1", source_sha256=SHA_A, parser_signature="s" * 64)

        with pytest.raises(ValueError, match="current_signature"):
            decide_rerun(
                active=active, current_source_sha256=SHA_A, current_signature=UNKNOWN_SIGNATURE
            )

    def test_unknown_source_sha_fails_closed(self):
        """同理：源指纹缺失时不得判「参数没变」。"""
        active = VersionIdentity(
            job_id="j1", source_sha256=SHA_A, parser_signature=UNKNOWN_SIGNATURE
        )

        with pytest.raises(ValueError, match="current_source_sha256"):
            decide_rerun(
                active=active, current_source_sha256=UNKNOWN_SIGNATURE, current_signature="s" * 64
            )
