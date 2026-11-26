import unittest

from overlay.gui.chat import mathimg
from overlay.gui.chat.render import MATH_SCHEME, Renderer, fallback_html, img_tag
from overlay.gui.theme import COLORS


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.r = Renderer(COLORS)

    def test_inline_markdown(self):
        html = self.r.render("**bold** and `code` and *em*").html
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<code style=", html)
        self.assertIn(">code</code>", html)
        self.assertIn("<em>em</em>", html)

    def test_question_line_is_highlighted(self):
        html = self.r.render("❓ What is GIL?\n\nA lock.").html
        self.assertIn("font-weight:600", html)
        self.assertIn("❓ What is GIL?", html)

    def test_code_block_is_highlighted_in_a_box(self):
        html = self.r.render("```python\nx = 1\n```").html
        self.assertIn("<table", html)
        self.assertIn("<pre", html)
        self.assertIn("<span style=", html)
        self.assertNotIn("class=", html)

    def test_unclosed_code_block_in_a_stream(self):
        html = self.r.render("text\n\n```\nx = 1").html
        self.assertIn("<pre", html)
        self.assertIn("x = 1", html)

    def test_html_in_the_answer_is_escaped(self):
        html = self.r.render("<script>alert(1)</script>").html
        self.assertNotIn("<script>", html)

    def test_table_gets_borders(self):
        html = self.r.render("| a | b |\n|---|---|\n| 1 | 2 |").html
        self.assertIn('border="1"', html)
        self.assertIn("<th bgcolor=", html)
        self.assertIn("<td>1</td>", html)

    def test_math_becomes_placeholders(self):
        out = self.r.render("Area $\\pi r^2$ and\n\n$$\\frac{a}{b}$$")
        self.assertEqual(len(out.math), 2)
        for url in out.math:
            self.assertTrue(url.startswith(MATH_SCHEME))
            self.assertIn(img_tag(url), out.html)
        spans = list(out.math.values())
        self.assertEqual(sorted(s.display for s in spans), [False, True])
        self.assertIn('<p align="center">', out.html)

    def test_math_inside_code_is_left_alone(self):
        out = self.r.render("Use `$HOME` and $x$")
        self.assertEqual(len(out.math), 1)
        self.assertIn("$HOME", out.html)

    def test_latex_fence_becomes_plain_block(self):
        html = self.r.render("```latex\n\\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}\n```").html
        self.assertIn("( 1  2 )", html)

    def test_fallbacks(self):
        inline = fallback_html(next(iter(self.r.render("$\\alpha^2$").math.values())), COLORS)
        self.assertIn("α²", inline)
        matrix = fallback_html(
            next(iter(self.r.render("$\\begin{bmatrix} 1 \\\\ 2 \\end{bmatrix}$").math.values())), COLORS
        )
        self.assertIn("<pre", matrix)
        self.assertIn("[ 1 ]", matrix)


class MathImageTests(unittest.TestCase):
    def test_simple_formula_renders_to_png(self):
        png = mathimg.render_png("\\frac{a}{b} + \\sqrt{x}", "#ffffff", 10.0, 96)
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_unsupported_latex_returns_none(self):
        self.assertIsNone(mathimg.render_png("\\begin{pmatrix} 1 \\end{pmatrix}", "#ffffff", 10.0, 96))
        self.assertIsNone(mathimg.render_png("", "#ffffff", 10.0, 96))


if __name__ == "__main__":
    unittest.main()
