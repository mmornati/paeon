// Draws the mermaid blocks of a postmortem. Loaded only on a page that has one, after the
// bundled library (static/vendor/mermaid.min.js, served from this host like everything else).
// Without this script, or if a diagram does not parse, the block stays what it is in the file:
// its text.
(() => {
  const blocks = [...document.querySelectorAll(".md pre > code.language-mermaid")].map((code) => ({
    pre: code.parentElement,
    text: code.textContent,
    figure: null,
  }));
  if (!blocks.length || !window.mermaid) return;

  const dark = () =>
    (document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";

  let round = 0;
  async function draw() {
    round += 1;
    // "strict" is the library's default; said here because the text may be a model's.
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: dark() ? "dark" : "neutral",
      fontFamily: getComputedStyle(document.body).fontFamily,
    });
    for (const [index, block] of blocks.entries()) {
      let svg;
      try {
        ({ svg } = await window.mermaid.render(`diagram-${round}-${index}`, block.text));
      } catch {
        // The library leaves its own error drawing behind; the text of the block says more.
        document.getElementById(`ddiagram-${round}-${index}`)?.remove();
        block.pre.classList.add("md-diagram-failed");
        continue;
      }
      const figure = document.createElement("figure");
      figure.className = "md-diagram";
      // Parsed apart from this page, then adopted: the library has already sanitised it.
      const drawn = new DOMParser().parseFromString(svg, "text/html").body.firstElementChild;
      if (!drawn) continue;
      figure.append(drawn);
      (block.figure || block.pre).replaceWith(figure);
      block.figure = figure;
    }
  }

  draw();
  // Drawn again when the scheme changes: a diagram carries its own colours.
  new MutationObserver(draw).observe(document.documentElement, { attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
})();
