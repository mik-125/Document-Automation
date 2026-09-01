from __future__ import annotations

import re
import unittest
from pathlib import Path


class ModalScrollIsolationContractTests(unittest.TestCase):
    """Protect modal scrolling without adding a JavaScript test dependency."""

    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent
        cls.javascript = (root / "app.js").read_text(encoding="utf-8")
        cls.styles = (root / "styles.css").read_text(encoding="utf-8")

    @classmethod
    def function_section(cls, start: str, end: str) -> str:
        start_index = cls.javascript.index(f"function {start}(")
        end_index = cls.javascript.index(f"function {end}(", start_index)
        return cls.javascript[start_index:end_index]

    @classmethod
    def style_rule(cls, selector: str) -> str:
        match = re.search(
            rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}",
            cls.styles,
        )
        if not match:
            raise AssertionError(f"Missing CSS rule for {selector}")
        return match.group("body")

    @classmethod
    def javascript_functions(cls) -> dict[str, str]:
        starts = list(
            re.finditer(
                r"(?:^|\n)function\s+(?P<name>[A-Za-z_$][\w$]*)\(",
                cls.javascript,
            )
        )
        return {
            match.group("name"): cls.javascript[
                match.start() : starts[index + 1].start()
                if index + 1 < len(starts)
                else len(cls.javascript)
            ]
            for index, match in enumerate(starts)
        }

    def test_open_and_close_share_one_body_scroll_lock(self) -> None:
        opened = self.function_section("openModal", "closeModal")
        closed = self.function_section("closeModal", "emptyState")
        functions = self.javascript_functions()
        body_locks: list[tuple[str, str]] = []
        for function_name, source in functions.items():
            lock = re.search(
                r'document\.body\.classList\.(?:add|toggle)\(\s*["\'](?P<name>[\w-]+)["\']',
                source,
            )
            if lock:
                body_locks.append((function_name, lock.group("name")))
        reachable_locks = [
            (owner, name)
            for owner, name in body_locks
            if owner == "openModal"
            or re.search(rf"\b{re.escape(owner)}\(\)", opened)
        ]
        self.assertTrue(
            reachable_locks,
            "Opening any modal must apply a body class that locks background scrolling.",
        )
        _, lock_name = reachable_locks[0]

        unlock_owners = [
            function_name
            for function_name, source in functions.items()
            if re.search(
                rf'document\.body\.classList\.(?:remove|toggle)\(\s*["\']{re.escape(lock_name)}["\']',
                source,
            )
        ]
        self.assertTrue(
            any(
                owner == "closeModal"
                or re.search(rf"\b{re.escape(owner)}\(\)", closed)
                for owner in unlock_owners
            ),
            "Closing the modal must always release the same body scroll lock.",
        )
        body_lock = re.search(
            rf"(?:^|\n)\s*body\.{re.escape(lock_name)}(?:\s|,|\{{)[^{{]*\{{(?P<body>[^}}]*)\}}",
            self.styles,
        )
        self.assertIsNotNone(body_lock, "The modal body class needs a CSS rule.")
        self.assertRegex(body_lock.group("body"), r"overflow(?:-y)?\s*:\s*hidden\b")

    def test_every_backdrop_close_path_uses_central_cleanup(self) -> None:
        closed = self.function_section("closeModal", "emptyState")
        close_start = self.javascript.index("function closeModal(")
        close_end = close_start + len(closed)
        direct_hides = list(
            re.finditer(
                r'\$\(["\']#modal-backdrop["\']\)\.classList\.add\(["\']hidden["\']\)',
                self.javascript,
            )
        )
        self.assertTrue(direct_hides, "closeModal must hide the backdrop.")
        self.assertTrue(
            all(close_start <= match.start() < close_end for match in direct_hides),
            "No close path may bypass closeModal and leave the body locked.",
        )

        click_handler = self.javascript[
            self.javascript.index('document.addEventListener("click"') :
            self.javascript.index('document.addEventListener("change"')
        ]
        self.assertRegex(
            click_handler,
            r"""const\s+close\s*=\s*event\.target\.closest\(["']\[data-close-modal\]["']\)"""
            r"[\s\S]{0,180}?closeModal\(\)",
        )
        self.assertRegex(
            click_handler,
            r'event\.target\s*===\s*\$\(["\']#modal-backdrop["\']\)'
            r"[\s\S]{0,140}?closeModal\(\)",
        )

    def test_modal_panel_is_the_contained_scroll_container(self) -> None:
        panel = self.style_rule(".modal")
        backdrop = self.style_rule(".modal-backdrop")
        self.assertRegex(panel, r"max-height\s*:\s*calc\([^;]*(?:vh|dvh)[^;]*\)")
        self.assertRegex(panel, r"overflow-y\s*:\s*(?:auto|scroll)\b")
        self.assertRegex(
            panel,
            r"overscroll-behavior(?:-y)?\s*:\s*contain\b",
            "Scrolling at the modal edges must not move the page behind it.",
        )
        self.assertNotRegex(
            backdrop,
            r"overflow-y\s*:\s*(?:auto|scroll)\b",
            "The panel, not the full-screen backdrop, must own modal scrolling.",
        )

    def test_backdrop_and_panel_remain_visually_separate(self) -> None:
        backdrop = self.style_rule(".modal-backdrop")
        panel = self.style_rule(".modal")
        background = re.search(
            r"background\s*:\s*rgba?\((?P<channels>[^)]*)\)", backdrop
        )
        self.assertIsNotNone(background, "The backdrop needs a visible dark overlay.")
        channels = [part.strip() for part in background.group("channels").split(",")]
        if len(channels) == 4:
            self.assertGreaterEqual(float(channels[-1]), 0.5)
        self.assertRegex(backdrop, r"backdrop-filter\s*:\s*blur\([^)]*[1-9][0-9.]*px\)")
        self.assertRegex(panel, r"background\s*:\s*(?:#fff(?:fff)?|white|var\(--paper\))\b")
        self.assertRegex(panel, r"box-shadow\s*:\s*[^;]+")


if __name__ == "__main__":
    unittest.main()
