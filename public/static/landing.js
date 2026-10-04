// The landing page shows real recipe cards from the archive, so the first thing a visitor sees
// is a real dish in a real person's words. If the archive is empty or offline, the drawing stays.
(function () {
  const art = document.getElementById("recipeArt");
  if (!art) return;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  Promise.all([fetch("/api/recipes").then((r) => r.json()), fetch("/api/family").then((r) => r.json())])
    .then(([recipes, family]) => {
      if (!Array.isArray(recipes) || !recipes.length) return;
      const pick = recipes.find((r) => (r.recipe.tips || []).length) || recipes[0];
      const other = recipes.find((r) => r !== pick);
      const rec = pick.recipe;
      const who = family.elder_name || "their";
      const tip = (rec.tips || [])[0];
      const ingredients = rec.ingredients.slice(0, 4).map((i) => `<li>${esc(i)}</li>`).join("");
      const more = rec.ingredients.length > 4 ? `<li class="more">and ${rec.ingredients.length - 4} more</li>` : "";
      art.innerHTML = `
        <div class="live-stack">
          ${other ? `<div class="live-card back"><span class="live-kicker">${esc(who)}'s kitchen</span><h4>${esc(other.recipe.name)}</h4></div>` : ""}
          <a class="live-card" href="/app#memo/${esc(pick.memo_id)}">
            <span class="live-kicker">${esc(who)}'s kitchen</span>
            <h4>${esc(rec.name)}</h4>
            <ul>${ingredients}${more}</ul>
            ${tip ? `<p class="live-tip">${esc(tip)}</p>` : ""}
            <div class="live-foot">
              <img src="/api/qr/${esc(pick.memo_id)}" alt="QR code that plays ${esc(who)} explaining ${esc(rec.name)}" width="64" height="64">
              <span>Scan to hear ${esc(who)} tell it</span>
            </div>
          </a>
        </div>`;
      const caption = document.getElementById("recipeCount");
      if (caption) caption.textContent = `${recipes.length} recipe${recipes.length === 1 ? "" : "s"} in ${who}'s book so far.`;
    })
    .catch(() => {});
})();
