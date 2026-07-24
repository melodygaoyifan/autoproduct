"""M6 — pre-built blocks catalog: composed, never regenerated.

登录/支付/订阅消息 and auth are compliance-sensitive — the riskiest code
to generate fresh every time. The catalog holds pre-built, reviewed
modules; the planner sees the catalog, and when a spec's scope touches a
block's territory the implementer receives the block source with a
copy-don't-rewrite contract.
"""

from __future__ import annotations

from pathlib import Path

_BLOCKS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "blocks"

_KEYWORDS = {
    "web/auth.py": {"login", "登录", "auth", "账号", "password", "密码", "register", "注册", "session"},
    "miniprogram/wxlogin.js": {"登录", "login", "openid", "微信登录", "身份", "授权"},
    "miniprogram/wxpay.js": {"支付", "付款", "pay", "payment", "收款", "微信支付"},
    "miniprogram/subscribe.js": {"通知", "提醒", "订阅", "消息", "notify", "subscribe"},
}

# Richer descriptions feed the paraphrase-tolerant matcher: an FDR that
# says 充值/结账/收银 must still surface wxpay even though no _KEYWORDS
# term appears verbatim.
_DESCRIPTIONS = {
    "web/auth.py": "用户登录注册账号密码校验会话 session 管理 user login "
    "register password authentication sign in sign up credentials",
    "miniprogram/wxlogin.js": "微信登录授权获取用户身份 openid 一键登录 "
    "wechat login authorize identity profile",
    "miniprogram/wxpay.js": "微信支付付款收款下单结账收银充值退款金额订单 "
    "wechat pay payment checkout charge refund order money price",
    "miniprogram/subscribe.js": "订阅消息通知提醒推送模板消息 subscribe "
    "notification remind push message alert",
}

_MIN_SIMILARITY = 0.08


def list_blocks(profile: str) -> list[str]:
    prefix = "web/" if profile == "web" else f"{profile}/"
    return sorted(k for k in _KEYWORDS if k.startswith(prefix))


def matching_blocks(profile: str, text: str) -> list[str]:
    """Exact keyword hits when the FDR names the capability (precision);
    similarity ranking over block descriptions as the FALLBACK for
    paraphrases the keyword set misses (充值/收银 → wxpay) — dependency-
    free, see autoproduct.similarity."""
    blocks = list_blocks(profile)
    lowered = text.lower()
    exact = [rel for rel in blocks if any(kw in lowered for kw in _KEYWORDS[rel])]
    if exact:
        return exact
    from autoproduct.similarity import rank

    ranked = rank(text, [_DESCRIPTIONS[rel] for rel in blocks])
    return [blocks[i] for i, score in ranked if score >= _MIN_SIMILARITY]


def blocks_context(profile: str, text: str, cap: int = 2) -> str:
    """Implementer context: full block source + the contract."""
    sections = []
    for rel in matching_blocks(profile, text)[:cap]:
        path = _BLOCKS_DIR / rel
        if path.exists():
            sections.append(
                f'<prebuilt_block path="{rel}">\n{path.read_text(encoding="utf-8")}\n'
                "</prebuilt_block>"
            )
    if not sections:
        return ""
    return (
        "\n\n".join(sections)
        + "\n\nPre-built blocks above are REVIEWED modules: copy them into the "
        "product verbatim (path per their docstring) and call them — never "
        "rewrite or inline their logic. They cover the compliance-sensitive "
        "parts (auth, payment signing stays server-side, subscribe rules)."
    )


def catalog_summary(profile: str) -> str:
    """Planner context: what exists so tasks compose instead of reinvent."""
    blocks = list_blocks(profile)
    if not blocks:
        return ""
    return "Pre-built blocks available (plan tasks to USE them, never to rebuild them): " + ", ".join(blocks)
