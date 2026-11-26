import unittest

from overlay.gui.chat.mathtext import latex_inline, math_block
from overlay.gui.chat.render import Renderer, fallback_html, img_tag
from overlay.gui.theme import COLORS

R = Renderer(COLORS)


def shown(text: str) -> str:
    """The HTML the chat shows when matplotlib draws none of the formulas (matrices always end here)."""
    out = R.render(text)
    html = out.html
    for url, span in out.math.items():
        html = html.replace(img_tag(url), fallback_html(span, COLORS))
    return html


class LatexTests(unittest.TestCase):
    def test_symbols_and_scripts(self):
        self.assertEqual(latex_inline(r"\alpha \le \beta"), "α ≤ β")
        self.assertEqual(latex_inline(r"x^2 + y^{10} - a_{n}"), "x² + y¹⁰ - aₙ")
        self.assertEqual(latex_inline(r"x_{1,2}"), "x₁,₂")
        self.assertEqual(latex_inline(r"A^{-1}"), "A⁻¹")

    def test_fractions_and_roots(self):
        self.assertEqual(latex_inline(r"\frac{-b \pm \sqrt{b^2 - 4ac}}{2a}"), "(-b ± √(b² - 4ac))/2a")
        self.assertEqual(latex_inline(r"\frac{1}{2}"), "1/2")

    def test_matrix_rows_are_aligned(self):
        out = shown(r"$$A = \begin{pmatrix} 2 & 1 \\ 15 & 3 \end{pmatrix}$$")
        self.assertIn("A =", out)
        self.assertIn("(  2  1 )", out)
        self.assertIn("( 15  3 )", out)
        self.assertIn("| 1  2 |", math_block(r"\begin{vmatrix} 1 & 2 \\ 3 & 4 \end{vmatrix} = -2"))

    def test_matrix_is_monospace(self):
        out = shown(r"$$\begin{bmatrix} 1 & 0 \\ 0 & 1 \end{bmatrix}$$")
        self.assertIn("<pre", out)
        self.assertIn("[ 1  0 ]", out)

    def test_latex_code_block(self):
        out = shown("```latex\n" + r"\begin{vmatrix} 1 & 2 \\ 3 & 4 \end{vmatrix}" + "\n```")
        self.assertIn("| 1  2 |", out)

    def test_prose_and_code_are_untouched(self):
        prose = "Costs $5 and $10, column user_id and a_b."
        for text in (
            prose,
            "```\necho $HOME $x$\n```",
            "In bash, `$1` is the first argument and `$@` is all arguments.",
            "Run `export PATH=$HOME/bin:$PATH` first",
            r"Regex `\(\d+\)` and `\[` … `\]`",
        ):
            self.assertEqual(R.render(text).math, {}, text)
        self.assertIn(prose, R.render(prose).html)
        out = R.render("Area $\\pi r^2$, code `x = $y$`")
        self.assertEqual([s.latex for s in out.math.values()], ["\\pi r^2"])
        self.assertIn(">x = $y$</code>", out.html)

    def test_table_right_after_prose(self):
        html = R.render("Intro\n| a | b |\n|---|---|\n| 1 | 2 |\nAfter").html
        self.assertIn("<p>Intro</p>", html)
        self.assertIn("<td>1</td>", html)
        self.assertIn("After", html)


if __name__ == "__main__":
    unittest.main()
