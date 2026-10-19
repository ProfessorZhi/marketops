"""Production binding of Crawl4AI to the existing restricted egress session."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

@dataclass(frozen=True)
class SessionEgressBinding:
    """把 A 的 :class:`SafeEgressSession` 适配成本 adapter 的出口接缝。

    **它不加任何一格配置。** ``browser_config_kwargs`` 原样转发会话给的那一份，
    ``proxy_url`` 取代理真正监听的地址，``decisions`` 取代理记下的判决。
    一个在这里「顺手补一格」的实现会让两处定义分叉，而分叉的那一天没有东西会红。

    ``check(url)`` 是**预检**：它问的是判据（``gate.check``），不是代理。
    两者不是一回事 —— 代理是连接被钉住的那个位置，预检只是让一次注定被拒的抓取
    不至于先把浏览器拉起来。
    """

    session: Any
    implementation: str
    contract_sha256: str

    @property
    def proxy_url(self) -> str:
        return str(self.session.proxy.url)

    def decisions(self) -> tuple[Any, ...]:
        return tuple(self.session.proxy.decisions)

    def check(self, url: str) -> Any:
        import research_adapter_crawl4ai as adapter

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        verdict = self.session.gate.check(host, port)
        return adapter.EgressDecisionView(
            method="GET", target=f"{host}:{port}", port=port,
            allowed=bool(verdict.allowed),
            refusal="" if verdict.allowed else str(verdict.refusal or ""),
            address=str(getattr(verdict, "address", "") or ""),
            detail=str(getattr(verdict, "detail", "") or ""),
        )

    def browser_config_kwargs(self) -> Mapping[str, object]:
        return self.session.browser_config_kwargs()

    # -- 判据用的读数 -----------------------------------------------------
    def refusal_targets(self) -> tuple[str, ...]:
        return tuple(d.target for d in self.session.proxy.decisions if not d.allowed)

    def allowed_targets(self) -> tuple[str, ...]:
        return tuple(d.target for d in self.session.proxy.decisions if d.allowed)

    def egress_ref(self) -> str:
        return str(self.session.browser_egress_ref())


def _contract_sha256(egress_module: Any) -> str:
    """A 那一层的**形状版本**摘要。

    取 ``SAFE_EGRESS_API_VERSION``（形状变了才变）+ 模块文件本身的字节 hash。
    没有这一格时，一个「同名但已经改了行为」的出口与它上一版长得一模一样，
    而 :func:`verify_egress_binding` 那一侧看不出区别。
    """
    version = str(getattr(egress_module, "SAFE_EGRESS_API_VERSION", "unknown"))
    path = getattr(egress_module, "__file__", "") or ""
    digest = hashlib.sha256()
    digest.update(version.encode("utf-8"))
    try:
        digest.update(Path(path).read_bytes())
    except OSError:
        digest.update(b"<unreadable>")
    return digest.hexdigest()


def binding_for(session: Any, egress_module: Any) -> SessionEgressBinding:
    return SessionEgressBinding(
        session=session,
        implementation="research_egress.SafeEgressSession",
        contract_sha256=_contract_sha256(egress_module),
    )
