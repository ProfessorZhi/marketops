"""W-E 的**真实本地**端到端判据：真 Chromium、真 Playwright、A 的真受管出口。

## 两条独立的东西，刻意放在同一个文件里

* :class:`HarnessSelfReportTests` —— **永远跑**，不启动浏览器。它回答的是
  「这一套 harness 现在能不能跑、差哪一格、该做什么」。一条只有
  ``skip`` 的判据在这台机器上是零信息的：读的人分不出
  「没装浏览器」与「测试写坏了」。
* :class:`LiveLocalCrawlTests` —— **要显式开关**（``MARKETOPS_LOCAL_CRAWL_E2E=1``）
  且依赖齐备才跑。它启动真的 Chromium。

## 它不碰公网

靶子是本进程自己起的回环夹具，诱饵名来自 RFC 2606 的保留域，合成解析器对任何
别的名字直接抛 ``NONEXISTENT`` —— 不向真实解析器提问，所以断网也跑得动，
而「出口拒绝了一个陌生名字」这件事是可复现的。

## 一条读法上的警戒

拒绝那几条判据的承重读数是 ``site.hits``：**没有长出来**才叫被拒。
一次「被拒」如果只写成 ``allowed is False``，它与「请求根本没发出去」
在报告上同形 —— 而后者什么也没证明。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_adapter_crawl4ai as adapter  # noqa: E402
import research_crawl4ai_live_local as live  # noqa: E402
from crawl4ai_fixtures import limits  # noqa: E402

OPT_IN = os.environ.get(live.LOCAL_CRAWL_E2E_ENV) == "1"


def _state() -> dict:
    return live.dependency_state()


def _dependencies_ready() -> bool:
    return _state()["verdict"] == "READY"


def _skip_reason() -> str:
    """为什么这一组现在不跑。**一句话，带上该做什么。**

    依赖状态在**收集期**算一次并写进 reason：一条只写「skipped」的判据在这台
    机器上是零信息，读的人分不出「没装浏览器」与「测试写坏了」。
    """
    if not OPT_IN:
        return f"真实 Chromium 端到端要显式开关：{live.LOCAL_CRAWL_E2E_ENV}=1"
    state = _state()
    if state["verdict"] == "READY":
        return ""
    return f"依赖未就绪：{state['verdict']} —— {state['action']}"


_LIVE_CLASS_SKIP = _skip_reason()


class HarnessSelfReportTests(unittest.TestCase):
    """**永远跑**：把「能不能跑」拆开报，而不是变成一个静默的 skip。"""

    def test_verdict_is_from_the_closed_set(self):
        state = _state()
        self.assertIn(state["verdict"], live.DEPENDENCY_VERDICTS)

    def test_每一条不是_READY_的结论都带着一个可执行的下一步(self):
        state = _state()
        if state["verdict"] == "READY":
            self.assertEqual("", state["action"])
        else:
            # 一个只有结论没有动作的报告，读的人得自己去猜该装什么。
            self.assertTrue(state["action"].strip(), state)
            self.assertIn(state["verdict"].split("_")[0], state["action"].upper()
                          .replace("W-A", "SAFE")
                          .replace("CRAWL4AI", "CRAWL4AI")
                          .replace("CHROMIUM", "CHROMIUM")
                          .replace("PLAYWRIGHT", "PLAYWRIGHT"))

    def test_state_reports_every_格_separately(self):
        state = _state()
        for key in ("safe_egress_importable", "research_egress_importable",
                    "crawl4ai_importable", "chromium_present", "opt_in",
                    "safe_egress_scripts_env", "safe_egress_scripts_set"):
            self.assertIn(key, state)

    def test_a_missing_safe_egress_is_its_own_verdict_not_a_crash(self):
        """依赖注入判「结论怎么算出来的」，而不必真的卸掉依赖。"""
        state = live.dependency_state(
            module_import=lambda name: None if name == "marketops_safe_egress" else True)
        self.assertEqual("SAFE_EGRESS_NOT_IMPORTABLE", state["verdict"])
        self.assertIn(live.SAFE_EGRESS_SCRIPTS_ENV, state["action"])

    def test_missing_browser_is_a_dependency_verdict_not_a_product_defect(self):
        state = live.dependency_state(
            module_import=lambda name: True,
            playwright_module=_NoChromiumPlaywright())
        self.assertEqual("CHROMIUM_NOT_INSTALLED", state["verdict"])
        self.assertIn("playwright install chromium", state["action"])

    def test_the_loader_names_the_variable_it_needs(self):
        """取不到依赖时必须说出**修复动作**，不是一个裸回溯。

        判的是**那句话**，不是「加载器会不会抛」：模块一旦进过 ``sys.modules``，
        ``import_module`` 就会命中缓存，于是「抛没抛」在这里恒为假 ——
        一条永远绿的负向判据比没有更坏。
        """
        hint = live.safe_egress_missing_hint()
        self.assertIn(live.SAFE_EGRESS_SCRIPTS_ENV, hint)
        self.assertIn("research_egress", hint)

    def test_the_loader_returns_both_modules_when_they_are_present(self):
        if not (_state()["safe_egress_importable"]
                and _state()["research_egress_importable"]):
            self.skipTest("依赖不在本机 —— 这一条只判正常的取回")
        safe, egress = live.load_safe_egress()
        self.assertTrue(hasattr(safe, "EgressGate"))
        self.assertTrue(hasattr(egress, "safe_browser_egress"))

    def test_fixture_html_carries_every_subresource_kind(self):
        """夹具页必须真的发出这几类请求，否则那几条判据是空跑。

        判的是**常量本身**（``FIXTURE_BODY``），不是某一台起起来的站点：
        「这份 fixture 覆盖了哪几类网络动作」是一个关于源码的事实，
        而把它搭在一次服务启动上会让这条判据在被跳过时读起来像「测过了」。
        """
        body = live.FIXTURE_BODY
        for probe in ("new Image()", "createElement('iframe')", "new Worker(",
                      "fetch(", "new WebSocket("):
            self.assertIn(probe, body, probe)
        self.assertEqual(5, len(live.SUBRESOURCE_KINDS))


@unittest.skipIf(bool(_LIVE_CLASS_SKIP), _LIVE_CLASS_SKIP)
class ProductionPolicyRefusalTests(unittest.TestCase):
    """**不注入任何窄门**：走 A 的**生产判据**，每一个坏 URL 都必须零连接。

    与 :class:`LiveLocalCrawlTests` 的分工是**判据口径**，不是被测物：

    * 那边为了能在本机立起一个靶子，用 A 自己提供的可注入判据（``gate=``），
      只对夹具那一个 ``host:port`` 点头 —— 它证明的是**管道**；
    * 这边**不带 gate**，``safe_browser_egress`` 于是构造的是
      ``EgressGate(resolver, allowlist=…)``，也就是生产路径上的那一份判据 ——
      它证明的是「坏目标在**建立连接之前**就被拒」。

    后者才是「SSRF 之下零连接」那一句的落点。靶子是一个**只数连接、不应答**的
    回环 listener：它 accept 一次记一次 —— 「出口拒了它」与「根本没有连过去」
    在报告上同形，只有这个计数器能把两者分开。
    """

    @classmethod
    def setUpClass(cls):
        cls.safe, cls.egress = live.load_safe_egress()

    # -- 用例表 -----------------------------------------------------------
    def _cases(self, decoy_port: int):
        """``(名字, URL, 期望的理由片段, 会不会打到计数器)``。

        端口这一格是**实测撞出来的**：A 的判据只允许 ``80`` / ``443``，所以
        任何带临时端口的 URL 在 **DNS 之前**就被 ``port_not_allowed`` 拦下 ——
        第一版把 DNS rebinding 的用例写在临时端口上，于是它们一次都没走到
        ``check_resolved``，而「通过」看起来像 rebinding 被挡住了。
        现在两类分开：**端口/字面量**那类用计数器端口（能直接证明零连接），
        **DNS** 那类用 80（能走到解析判据）。
        """
        return [
            # --- 能打到计数器：证明「零连接」 -------------------------------
            ("loopback-ip", f"http://127.0.0.1:{decoy_port}/x", "private_target", True),
            ("localhost-name", f"http://localhost:{decoy_port}/x", "reserved_host", True),
            ("port-not-allowed", f"http://rebind.example.org:{decoy_port}/x",
             "port_not_allowed", True),
            # --- DNS 那一格：用 80，才能走到 check_resolved -----------------
            ("metadata-via-dns", "http://metadata.example.org/x",
             "resolves_to_private", False),
            ("rebind-via-dns", "http://rebind.example.org/x",
             "resolves_to_private", False),
            ("private-rfc1918", "http://private.example.org/x",
             "resolves_to_private", False),
            ("redirect-target-loopback", "http://rebind.example.org/redir",
             "resolves_to_private", False),
            # --- 字面量元数据地址（不需要端口）-----------------------------
            ("metadata-ip", "http://169.254.169.254/latest/meta-data/",
             "private_target", False),
        ]

    def _resolver(self, decoy_port: int):
        """合成解析器。**名字必须落在非保留后缀上，否则测不到 DNS 那一格。**

        本机实测撞到过：第一版用 ``rebind.example``，而 A 的
        ``RESERVED_HOST_SUFFIXES`` 含 ``.example`` —— 于是拒绝来自**字面量**判据
        （``reserved_host``），DNS 根本没被问过。那次「通过」看起来像 rebinding
        被挡住了，其实那个用例一次都没测到 rebinding。``example.org`` 是 A
        刻意**不**列入保留的名字空间（它会让人把文档里的示例 URL 读成非法目标）。
        """
        Synthetic = live.synthetic_resolver_class(self.safe)
        return Synthetic({
            "fixture.example.org": "127.0.0.1",
            "metadata.example.org": "169.254.169.254",
            "rebind.example.org": "127.0.0.1",
            "private.example.org": "10.0.0.5",
        })

    def _navigate(self, url: str, *, decoy_port: int):
        import asyncio

        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        resolver = self._resolver(decoy_port)
        allowlist = frozenset({"fixture.example.org", "metadata.example.org",
                               "rebind.example.org", "private.example.org"})
        # **没有 gate=**：这就是生产判据。
        with self.egress.safe_browser_egress(allowlist=allowlist,
                                             resolver=resolver) as session:
            kwargs = session.browser_config_kwargs()

            async def run():
                config = CrawlerRunConfig(page_timeout=20000, cache_mode="BYPASS",
                                          max_retries=0)
                async with AsyncWebCrawler(
                        config=BrowserConfig(**kwargs, headless=True)) as crawler:
                    return await crawler.arun(url=url, config=config)

            result = asyncio.run(run())
            return result, session

    # -- 判据 -------------------------------------------------------------
    def test_every_bad_target_is_refused_with_zero_connections(self):
        with live.ConnectionCounter() as counter:
            for name, url, expected, hits_counter in self._cases(counter.port):
                with self.subTest(case=name):
                    result, session = self._navigate(url, decoy_port=counter.port)
                    refusals = [d for d in session.proxy.decisions if not d.allowed]
                    allowed = [d for d in session.proxy.decisions if d.allowed]
                    self.assertTrue(refusals,
                                    f"{name}: 出口上一条拒绝都没有 —— 请求可能根本没经过代理")
                    joined = " ".join(f"{r.refusal} {r.detail}" for r in refusals)
                    self.assertIn(expected, joined, f"{name}: 拒绝理由不是预期的那一格")
                    # 出口上**没有任何一条放行**：拒绝必须发生在建立连接之前。
                    self.assertEqual([], allowed, f"{name}: 出口放行了这一跳")
                    if hits_counter:
                        # **承重读数**：靶子上一次连接都没有。
                        self.assertEqual([], counter.connections,
                                        f"{name}: 目标收到了连接 —— 判据没有在连接之前挡住")
                    # 页面也不该被当成一份可用材料。
                    self.assertFalse(bool(getattr(result, "success", False))
                                     and (getattr(result, "html", "") or "").strip(),
                                     f"{name}: 被拒的目标产生了看起来可用的页面")

    def test_the_counter_itself_is_not_vacuous(self):
        """反证：这个计数器**确实**能记到连接。

        少了这一条，上面那个「零连接」可能只是因为计数器根本没在工作 ——
        一个永远记不到东西的仪器与「世界很干净」在报告上同形。
        """
        import socket

        with live.ConnectionCounter() as counter:
            sock = socket.create_connection(("127.0.0.1", counter.port), timeout=5)
            sock.close()
            for _ in range(50):
                if counter.connections:
                    break
                import time as _time
                _time.sleep(0.02)
        self.assertEqual(1, len(counter.connections))

    def test_a_websocket_target_is_refused_by_the_same_gate(self):
        """WebSocket 在**判据**这一层的落点。``ws://`` 的 host/port 走同一份 ``check``。

        页面内发的 WebSocket 已经在窄门那一组里判过（``/page`` 的 JS 会拉
        ``ws://decoy.example``）；这一条判的是**生产判据**对同一个目标的回答，
        与浏览器是否参与无关。
        """
        resolver = self._resolver(8080)
        gate = self.safe.EgressGate(resolver,
                                    allowlist=frozenset({"rebind.example.org"}))
        verdict = gate.check("rebind.example.org", 8080)
        self.assertFalse(verdict.allowed)
        self.assertIn("resolves_to_private", str(verdict.refusal))


class _ChromiumStub:
    executable_path = "/nonexistent/ms-playwright/chromium-0/chrome-linux64/chrome"


class _PlaywrightManagerStub:
    """``sync_playwright()`` 的替身：只回答 ``chromium.executable_path``。"""

    chromium = _ChromiumStub()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None


class _NoChromiumPlaywright:
    """一个「playwright 在、浏览器不在」的假模块，用来判结论怎么算出来的。"""

    class sync_api:  # noqa: N801 -  mimics the real module layout
        @staticmethod
        def sync_playwright():
            return _PlaywrightManagerStub()



@unittest.skipIf(bool(_LIVE_CLASS_SKIP), _LIVE_CLASS_SKIP)
class LiveLocalCrawlTests(unittest.TestCase):
    """真 Chromium × A 的真出口 × 合成夹具。**无公网、无凭据、无付费。**"""

    @classmethod
    def setUpClass(cls):
        cls.safe, cls.egress = live.load_safe_egress()

    def _run(self, capability, params, *, site, crawl4ai=None,
             artifact_root=None, **limits_over):
        import importlib

        module = crawl4ai if crawl4ai is not None else importlib.import_module("crawl4ai")
        with live.live_local_session(safe=self.safe, egress=self.egress, site=site) as session:
            binding = live.binding_for(session, self.egress)
            instance = live.adapter_for(binding, crawl4ai_module=module,
                                        artifact_root=artifact_root)
            request = _Request(capability, {**params, "limits": limits(**limits_over)})
            return instance.execute(request), session, binding

    # -- 形状 -------------------------------------------------------------
    def test_the_real_session_binding_passes_the_adapter_shape_check(self):
        with live.LiveFixtureSite() as site:
            with live.live_local_session(safe=self.safe, egress=self.egress,
                                         site=site) as session:
                binding = live.binding_for(session, self.egress)
                self.assertEqual((), adapter.verify_egress_binding(binding))
                kwargs = binding.browser_config_kwargs()
                self.assertEqual(binding.proxy_url, kwargs["proxy_config"]["server"])
                self.assertIn(adapter.LOOPBACK_BYPASS_GUARD, kwargs["extra_args"])

    def test_the_egress_proxy_is_stopped_when_the_session_exits(self):
        """会话退出之后，那个回环代理**不再监听** —— teardown 是一条判据，不是一句声明。

        一次验收留下的监听与一次真实的端口占用在 `ss` 上同形，而后者会在下一次
        运行里变成一个莫名其妙的连接失败。所以这里从里面记下端口，出来再连一次：
        连得上就是没停。
        """
        import socket

        with live.LiveFixtureSite() as site:
            with live.live_local_session(safe=self.safe, egress=self.egress,
                                         site=site) as session:
                proxy_url = session.proxy.url
            host_port = proxy_url.split("://", 1)[1]
            host, _, port = host_port.rpartition(":")
            with self.assertRaises(OSError):
                socket.create_connection((host, int(port)), timeout=2).close()

    # -- 真渲染 -----------------------------------------------------------
    def test_real_browser_renders_js_through_the_proxy(self):
        with live.LiveFixtureSite() as site:
            outcome, session, _ = self._run("crawl4ai.fetch_page",
                                            {"url": site.url("/page"), "extraction": {"mode": "markdown"}},
                                            site=site)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual(["/page"], site.hits)
            # JS 真的跑了：标记只存在于**渲染后**的 DOM 里。
            self.assertIn("JS_RENDER_MARK", outcome.payload["text"])
            # 出口上一定记着夹具那一跳。**不**断言「只有那一跳」—— 子资源由另一条
            # 判据负责，把两件事塞进一条会让失败时读不出是哪一件坏了。
            self.assertIn(site.origin.replace("http://", ""),
                          list(binding_targets(session)))

    def test_real_browser_structured_extraction_returns_real_values(self):
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run("crawl4ai.extract", {
                "url": site.url("/page"),
                "extraction": {"mode": "structured", "fields": [
                    {"name": "title", "kind": "css", "selector": "#title"},
                    {"name": "org", "kind": "css", "selector": "#org"},
                    {"name": "phone", "kind": "css", "selector": ".contact"},
                    {"name": "region", "kind": "css", "selector": "table tr td"},
                    {"name": "rendered", "kind": "css", "selector": "#render"},
                ]},
            }, site=site)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            fields = outcome.payload["fields"]
            self.assertEqual("Synthetic Fixture Page", fields["title"])
            self.assertEqual("Example Coatings Ltd", fields["org"])
            self.assertEqual("+44 20 7946 0958", fields["phone"])
            # ``#render`` 的内容**只**由页面里的 JS 写进去 —— 它是「浏览器真的执行了
            # 脚本」这条在**抽取产物**上的那一份读数，而不是靠读 html 字符串得出的。
            self.assertEqual("JS_RENDER_MARK", fields["rendered"])

    def test_real_browser_screenshot_is_a_real_png_not_an_empty_artifact(self):
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run("crawl4ai.screenshot",
                                      {"url": site.url("/page"),
                                       "artifacts": {"screenshot": True}},
                                      site=site)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual(1, len(outcome.artifacts))
            ref = outcome.artifacts[0]
            self.assertEqual("screenshot", ref.kind)
            self.assertEqual(64, len(ref.sha256))
            # PNG 的魔数：一个 0 字节的「产物」不该被记成一次成功。
            self.assertNotEqual(adapter.content_sha256(b""), ref.sha256)

    # -- 拒绝方向 ---------------------------------------------------------
    def test_every_browser_subresource_to_the_decoy_is_refused_and_never_connected(self):
        """夹具页自己发起 img / iframe / worker / fetch / WebSocket。

        判据读的是 ``site.hits``：它只该长出一条 ``/page``。诱饵名上**一次连接
        都没有** —— 而且拒绝必须发生在**出口**上（``session.proxy.decisions`` 里
        看得到那些目标），不是因为我们碰巧从来没让浏览器联网。
        """
        with live.LiveFixtureSite() as site:
            outcome, session, binding = self._run(
                "crawl4ai.fetch_page",
                {"url": site.url("/page"), "extraction": {"mode": "text"}},
                site=site, max_pages=4)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual(["/page"], site.hits)
            decoy = f"{live.DECOY_HOST}:{site.port}"
            refused = binding.refusal_targets()
            self.assertTrue(refused, "出口上一条拒绝都没有 —— 子资源根本没有被发起")
            for target in refused:
                self.assertEqual(decoy, target)
            self.assertEqual([], [t for t in binding.allowed_targets()
                                  if t.startswith(live.DECOY_HOST)])
            self.assertNotIn(live.DECOY_HOST, " ".join(site.hits))

    def test_the_narrow_gate_still_delegates_everything_but_the_fixture(self):
        """一个「只对夹具点头」的说法，必须能证明它**确实**把别的交出去了。"""
        with live.LiveFixtureSite() as site:
            outcome, session, _ = self._run(
                "crawl4ai.fetch_page",
                {"url": site.url("/page"), "extraction": {"mode": "text"}},
                site=site, max_pages=4)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            gate = session.gate
            # 夹具那一跳会被问**两次**：一次是 adapter 的预检，一次是代理真的转发时。
            # 判据看的是「每一次窄放行都落在夹具上」，不是次数 —— 次数是实现细节。
            self.assertTrue(gate.narrow_calls, "窄判据一次都没被问到")
            self.assertEqual({f"{site.host}:{site.port}"}, set(gate.narrow_calls))
            self.assertTrue(gate.delegated_calls, "窄判据一次都没把请求交出去")
            for target in gate.delegated_calls:
                self.assertTrue(target.startswith(live.DECOY_HOST), target)

    def test_http_redirect_is_not_followed(self):
        with live.LiveFixtureSite() as site:
            outcome, _, binding = self._run("crawl4ai.fetch_page",
                                            {"url": site.url("/redir")}, site=site)
            self.assertEqual("FAILED", outcome.outcome)
            # **不是** SOURCE_ACCESS_BLOCKED：库报回来的状态码是那个 302（第一个响应），
            # 而承重的是「浏览器被带到别处去了」这一格。
            self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)
            self.assertIn(live.DECOY_HOST, outcome.payload["detail"])
            self.assertEqual(["/redir"], site.hits)

    def test_meta_refresh_is_not_followed(self):
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run("crawl4ai.fetch_page",
                                      {"url": site.url("/meta")}, site=site)
            self.assertEqual(["/meta"], site.hits)
            self.assertNotIn(live.DECOY_HOST, " ".join(site.hits))
            # 这一次的关键读数：浏览器被带走了，而**拿回来的那份文档不被当成材料**。
            self.assertEqual("FAILED", outcome.outcome)
            self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)
            # 目的地在 ``detail`` 里**应该**看得见 —— 那是这次拒绝要人看的那一句。
            # 不该出现的是**把出口的拒绝说明当成正文**：所以判的是 payload 里
            # 没有 material 那一格，而不是「这个字符串整个不出现」。
            self.assertIsNone(outcome.payload.get("text"))
            self.assertEqual({}, outcome.payload.get("fields", {}))
            self.assertIn(live.DECOY_HOST, outcome.payload["detail"])

    # -- 资源上限 ---------------------------------------------------------
    def test_page_cap_bounds_a_real_deep_crawl(self):
        with live.LiveFixtureSite() as site:
            outcome, session, _ = self._run(
                "crawl4ai.deep_crawl",
                {"seeds": [site.url("/page")], "allowed_domains": [site.host],
                 "extraction": {"mode": "text"}},
                site=site, max_pages=1, max_depth=1)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual(1, len(outcome.payload["pages"]))
            self.assertEqual("page_cap_reached", outcome.payload["stop_reason"])
            self.assertEqual(["/page"], site.hits)
            self.assertEqual(1, outcome.payload["meter"]["pages"])

    def test_off_domain_seed_is_refused_before_any_browser_work(self):
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run(
                "crawl4ai.deep_crawl",
                {"seeds": [f"http://{live.DECOY_HOST}/x"],
                 "allowed_domains": [site.host], "extraction": {"mode": "text"}},
                site=site)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual([], outcome.payload["pages"])
            self.assertEqual([], site.hits)

    # -- PDF 产物（真浏览器）-----------------------------------------------
    def test_real_pdf_artifact_is_a_real_pdf_not_an_empty_one(self):
        """PDF 那一格：**产物必须真的是 PDF**，而不是一个 0 字节的「成功」。

        本机实测（对着候选 cf8ff6e）那一版报的是 ``OK`` + 一个 sha256 等于
        ``sha256(b"")`` 的产物 —— 而机理是它从没在 run config 上请求过 ``pdf=True``
        （不请求时库给的 ``result.pdf`` 是 ``None``）。这一条判的是现在这一版**真的
        拿到了字节**。
        """
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run("crawl4ai.pdf",
                                      {"url": site.url("/page"),
                                       "artifacts": {"pdf": True}}, site=site)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual(1, len(outcome.artifacts))
            ref = outcome.artifacts[0]
            self.assertEqual("pdf", ref.kind)
            self.assertNotEqual(adapter.content_sha256(b""), ref.sha256)
            self.assertGreater(outcome.payload["meter"]["bytes"], 1000)
            self.assertEqual(1, outcome.payload["meter"]["artifacts"])

    def test_the_sdk_returns_no_pdf_unless_the_run_config_asks_for_one(self):
        """机理那一条：不请求就没有产物。判据连着**为什么**一起留下。"""
        import asyncio

        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        with live.LiveFixtureSite() as site:
            with live.live_local_session(safe=self.safe, egress=self.egress,
                                         site=site) as session:
                kwargs = session.browser_config_kwargs()

                async def crawl(path, **run_kwargs):
                    config = CrawlerRunConfig(page_timeout=60000, cache_mode="BYPASS",
                                              max_retries=0, **run_kwargs)
                    async with AsyncWebCrawler(
                            config=BrowserConfig(**kwargs, headless=True)) as crawler:
                        return await crawler.arun(url=site.url(path), config=config)

                without = asyncio.run(crawl("/page"))
                with_flag = asyncio.run(crawl("/page", pdf=True))
        self.assertIsNone(getattr(without, "pdf", None))
        payload = getattr(with_flag, "pdf", None)
        self.assertIsNotNone(payload)
        # 实测：**PDF 回来是 bytes**（截图那一格回的是 base64 字符串）。
        self.assertIsInstance(payload, bytes)
        self.assertTrue(payload.startswith(b"%PDF-"), payload[:8])

    def test_pdf_bytes_are_parseable_and_carry_the_fixture_text(self):
        """**产物要能被读出来，而且里面要是那一页的字。**

        只断言「有产物」与「sha 不是空串」是不够的 —— 本轮之前那两条都成立，
        而产物里装的是**出口的拒绝说明**。这一条把字节落到盘上、用 ``pypdf``
        真的解析一遍，并且要求夹具页上的字出现在里面。
        """
        try:
            import pypdf
        except ImportError:  # pragma: no cover - 只在缺库时发生
            self.skipTest("pypdf 不在这个解释器里 —— 无法解析产物的字节")
        import tempfile

        with live.LiveFixtureSite() as site, tempfile.TemporaryDirectory() as root:
            # **/clean**：与 /page 同形，但不发起任何子资源。用它是为了让「页面
            # 自己的字」这一条不被出口的拒绝说明污染 —— 那一条在下面单独判。
            outcome, session, _ = self._run(
                "crawl4ai.pdf",
                {"url": site.url("/clean"), "artifacts": {"pdf": True}},
                site=site, artifact_root=root)
            self.assertEqual("OK", outcome.outcome, outcome.payload)

            # 归因：请求的与最终落到的是同一个 URL，200，没有跳转。
            self.assertEqual(site.url("/clean"), outcome.payload["requested_url"])
            self.assertEqual(site.url("/clean"), outcome.payload["final_url"])
            self.assertEqual(200, outcome.payload["status_code"])
            self.assertFalse(outcome.payload["redirected"])

            ref = outcome.artifacts[0]
            self.assertTrue(outcome.payload["artifact_persisted"])
            with open(ref.ref, "rb") as handle:
                raw = handle.read()
            self.assertTrue(raw.startswith(b"%PDF-"), raw[:8])
            self.assertEqual(ref.sha256, adapter.content_sha256(raw))

            text = "\n".join(page.extract_text() or ""
                              for page in pypdf.PdfReader(ref.ref).pages)
            # 夹具页上真实存在的那两段字。JS 写入的那一格也要在 —— 它证明产物
            # 是**渲染之后**的那一页，不是 HTTP 原文。
            self.assertIn("Synthetic Fixture Page", text)
            self.assertIn("Example Coatings Ltd", text)
            self.assertIn("JS_RENDER_MARK", text)
            # 而**出口的拒绝说明**不该在里面。
            self.assertNotIn("保留名字空间", text)

    def test_the_receipt_attributes_the_refusals_that_happened_during_the_render(self):
        """合法页的一份 PDF 里**也会**出现出口的拒绝说明，如果它渲染了被拒的 iframe。

        本机实测：`/page` 的 JS 会拉 `<img>/<iframe>/Worker/fetch/WebSocket` 到诱饵，
        出口全部拒；Chromium 把那些 **拒绝应答渲染进页面**（iframe 尤其），而
        `pdf=True` 导出的是**浏览器看到的那一页** —— 于是 PDF 里既有页面自己的字，
        也有出口的拒绝说明。

        这不是「页面失败」（`/page` 那一次 `outcome: OK`），也不是一个能被删掉的
        症状：删掉它就得改浏览器怎么显示被拒的 iframe。所以判据要求的是
        **准确归因**：回执必须说出「这一次渲染期间出口拒绝了几个目标」，
        并明确那几段字**不是来源材料**。
        """
        import tempfile

        with live.LiveFixtureSite() as site, tempfile.TemporaryDirectory() as root:
            outcome, _, _ = self._run(
                "crawl4ai.pdf",
                {"url": site.url("/page"), "artifacts": {"pdf": True}},
                site=site, artifact_root=root)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            # 页面自己的字在。
            self.assertGreater(outcome.payload["meter"]["bytes"], 1000)
            # 而这一次渲染期间出口确实拒过东西，且回执说得出来。
            self.assertGreaterEqual(outcome.payload["egress_decisions"], 1)
            self.assertGreaterEqual(len(outcome.payload["egress_refusals"]), 1)
            targets = {item["target"].split(":")[0]
                       for item in outcome.payload["egress_refusals"]}
            self.assertEqual({live.DECOY_HOST}, targets)
            self.assertTrue(any("不是来源材料" in c for c in outcome.caveats),
                            outcome.caveats)

    def test_a_clean_page_records_zero_render_refusals(self):
        import tempfile

        with live.LiveFixtureSite() as site, tempfile.TemporaryDirectory() as root:
            outcome, _, _ = self._run(
                "crawl4ai.pdf",
                {"url": site.url("/clean"), "artifacts": {"pdf": True}},
                site=site, artifact_root=root)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual([], list(outcome.payload["egress_refusals"]))
            self.assertFalse(any("不是来源材料" in c for c in outcome.caveats))

    def test_pdf_of_a_redirected_page_is_refused_and_writes_nothing(self):
        """被拒的那一份**不落盘**：不留下一份可以被误读成证据的文件。"""
        import tempfile
        from pathlib import Path as _Path

        with live.LiveFixtureSite() as site, tempfile.TemporaryDirectory() as root:
            outcome, _, _ = self._run(
                "crawl4ai.pdf",
                {"url": site.url("/redir"), "artifacts": {"pdf": True}},
                site=site, artifact_root=root)
            self.assertEqual("FAILED", outcome.outcome)
            self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)
            self.assertEqual((), outcome.artifacts)
            self.assertEqual([], list(_Path(root).iterdir()))

    # -- 未成功页的配额收据（真浏览器）-------------------------------------
    def test_a_real_403_page_keeps_its_quota_reading(self):
        """一页真 403 之后，配额那一格**不能是空的**。

        本机实测（候选 cf8ff6e）：出口上明明白白记着那一跳（浏览器起来了、页面被
        取回了），而适配层报 ``quota_units_used: null`` —— 一个浏览器秒与一页配额
        被花掉，账上写着「什么都没消耗」。
        """
        with live.LiveFixtureSite() as site:
            outcome, session, _ = self._run("crawl4ai.fetch_page",
                                            {"url": site.url("/forbidden")}, site=site)
            self.assertEqual("FAILED", outcome.outcome)
            self.assertEqual("SOURCE_ACCESS_BLOCKED", outcome.reason)
            # 这一页**真的**被取回了：出口上有一条判决，站点见过它。
            self.assertEqual(["/forbidden"], site.hits)
            self.assertTrue(session.proxy.decisions)
            # 于是配额必须记下来。
            self.assertEqual(1, outcome.payload["meter"]["pages"])
            self.assertIsNotNone(outcome.quota_units_used)
            self.assertEqual("1", str(outcome.quota_units_used))

    # -- 多页：sitemap seeding 与有界深爬（真浏览器 / 真出口）---------------
    def test_sitemap_seeding_fetches_through_the_proxy_and_bounds_the_seeds(self):
        """seeder 那一跳是**辅助 HTTP**，所以它必须经出口 —— 这一条判的就是它。

        sitemap 里**故意**放一条不属于夹具的条目：真判据的 allowlist 只含夹具主机，
        所以跨域那条必须被拒，而它被拒的理由要留在 ``refused`` 里。
        """
        with live.LiveFixtureSite() as site:
            outcome, session, _ = self._run(
                "crawl4ai.sitemap_seed",
                {"sitemap_urls": [site.url("/sitemap.xml")],
                 "allowed_domains": [site.host]},
                site=site, max_pages=10)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            # 取回**真的**发生在站点上：sitemap 那一跳 + 每个 origin 一次 robots。
            self.assertEqual(["/sitemap.xml", "/robots.txt"], site.hits)
            self.assertEqual(1, len(outcome.payload["robots"]))
            self.assertEqual(site.origin, outcome.payload["robots"][0]["origin"])
            self.assertTrue(session.proxy.decisions)
            seeds = outcome.payload["seeds"]
            self.assertEqual(len(site.sitemap_paths), len(seeds))
            for path in site.sitemap_paths:
                self.assertIn(site.origin + path, seeds)
            refused = {item["url"]: item["reason"]
                       for item in outcome.payload["refused"]}
            self.assertIn(site.sitemap_foreign, refused)
            self.assertEqual("OFF_DOMAIN_SEED_REFUSED",
                             refused[site.sitemap_foreign])

    def test_robots_disallowed_seeds_are_refused_with_a_quota_receipt(self):
        """robots 说不行的那几条**不进种子**，而配额把 robots 那一跳也记上。

        拒绝是**逐条**写进 ``refused`` 的（理由 `ROBOTS_DISALLOWED`），
        取回 robots 的那一个请求算进 meter —— 一次「拒了四条」的说法如果不带
        它花掉的请求数，读起来就像什么都没做。
        """
        with live.LiveFixtureSite() as site:
            site.robots_body = "User-agent: *\nDisallow: /beta\nDisallow: /gamma\n"
            outcome, _, _ = self._run(
                "crawl4ai.sitemap_seed",
                {"sitemap_urls": [site.url("/sitemap.xml")],
                 "allowed_domains": [site.host]},
                site=site, max_pages=10)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            seeds = outcome.payload["seeds"]
            self.assertFalse(any("/beta" in s or "/gamma" in s for s in seeds), seeds)
            self.assertTrue(any("/alpha" in s for s in seeds), seeds)
            self.assertTrue(any("/delta" in s for s in seeds), seeds)
            refused = {item["url"]: item["reason"]
                       for item in outcome.payload["refused"]}
            disallowed = {url for url, reason in refused.items()
                          if reason == "ROBOTS_DISALLOWED"}
            self.assertEqual({site.origin + "/beta", site.origin + "/gamma"},
                             disallowed)
            # 配额里既有 sitemap 那一跳，也有 robots 那一跳。
            self.assertGreaterEqual(outcome.payload["meter"]["pages"], 2)

    def test_a_multi_page_deep_crawl_is_bounded_by_max_pages(self):
        """**多页**：夹具声明 4 个分区，限额 3 ⇒ 只抓 3 页就停。

        判据的承重读数是 ``site.hits``：它必须**恰好**是那 3 条，
        而 ``stop_reason`` 必须说得出是哪一个上限停的。
        """
        with live.LiveFixtureSite() as site:
            outcome, session, _ = self._run(
                "crawl4ai.deep_crawl",
                {"seeds": [site.url("/page")], "allowed_domains": [site.host],
                 "extraction": {"mode": "text"}},
                site=site, max_pages=3, max_depth=2)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            pages = [row["url"] for row in outcome.payload["pages"]]
            self.assertEqual(3, len(pages))
            self.assertEqual("/page", site.hits[0])
            self.assertEqual(sorted(pages), sorted(
                f"{site.origin}{p}" for p in site.hits))
            self.assertEqual(3, len(site.hits))
            self.assertEqual("page_cap_reached", outcome.payload["stop_reason"])
            self.assertEqual(3, outcome.payload["meter"]["pages"])
            # 每一页各自一行证据，各自一份 hash。
            self.assertEqual(3, len(outcome.payload["manifest"]))

    def test_depth_zero_seeds_the_plan_but_fetches_only_the_seed(self):
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run(
                "crawl4ai.deep_crawl",
                {"seeds": [site.url("/page")], "allowed_domains": [site.host],
                 "extraction": {"mode": "text"}},
                site=site, max_pages=5, max_depth=0)
            self.assertEqual(1, len(outcome.payload["pages"]))
            self.assertEqual(["/page"], site.hits)

    def test_multi_page_crawl_keeps_its_quota_and_refusals_apart(self):
        """多页那一次里，配额是**总计**，而拒绝是**逐条**。"""
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run(
                "crawl4ai.deep_crawl",
                {"seeds": [site.url("/page")], "allowed_domains": [site.host],
                 "extraction": {"mode": "text"}},
                site=site, max_pages=6, max_depth=1)
            self.assertEqual("OK", outcome.outcome, outcome.payload)
            self.assertEqual(outcome.payload["meter"]["pages"],
                             len(outcome.payload["pages"]))
            ok_pages = [r for r in outcome.payload["pages"] if r["outcome"] == "OK"]
            self.assertEqual(outcome.payload["meter"]["pages"], len(ok_pages))
            self.assertEqual("pages|bytes|seconds|artifacts",
                             outcome.payload["meter"]["units"])

    # -- 反编造 -----------------------------------------------------------
    def test_no_credential_or_decoy_marker_leaks_into_the_receipt(self):
        with live.LiveFixtureSite() as site:
            outcome, _, _ = self._run(
                "crawl4ai.fetch_page",
                {"url": site.url("/page"), "extraction": {"mode": "markdown"}},
                site=site, max_pages=4)
            rendered = adapter.render_outcome_json(outcome)
            for token in (live.DECOY_HOST, "iVBORw0KGgo", "password", "Bearer "):
                self.assertNotIn(token, rendered, token)

    def test_a_page_the_sdk_itself_doubts_is_not_reported_as_clean(self):
        """crawl4ai 0.9.3 的反爬启发式会对**小页面**打 ``success=False``。

        本机实测：那不是取回失败（``html`` 有内容、JS 跑过），而是库对内容的一个
        怀疑。这一条判的是 adapter 把它报成了**一个可区分的读数**，
        而不是混进 ``LOCAL_CRAWL_FAILED``。
        """
        with live.LiveFixtureSite() as site:
            small = site.page_html("<p>tiny</p>")
            # 直接问 adapter 的分类函数：夹具站点发的是同一份 HTML，
            # 但这里要判的是**分类**，不是那一次取回。
            self.assertEqual("SOURCE_ANTIBOT_HEURISTIC",
                             adapter._sdk_failure_code(
                                 adapter.ANTIBOT_ERROR_PREFIX + ": Structural: minimal_text"))
            self.assertEqual("LOCAL_CRAWL_FAILED", adapter._sdk_failure_code(""))
            self.assertIn("tiny", small)


# ---------------------------------------------------------------------------
# 局部的 duck-typed 请求：与 crawl4ai_fixtures.Request 同形，但不跨文件依赖
# ---------------------------------------------------------------------------

class _Request:
    def __init__(self, capability_id: str, params: dict) -> None:
        self.capability_id = capability_id
        self.params = dict(params)
        self.run_id = "live-local-run"
        self.operation_id = "live-local-op"
        self.account_ref = "local"
        self.intent_fingerprint = "live-local"
        self.grant_ref = None


def binding_targets(session) -> tuple[str, ...]:
    return tuple(d.target for d in session.proxy.decisions)


if __name__ == "__main__":
    unittest.main()
