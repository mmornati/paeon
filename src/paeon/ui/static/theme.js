// Runs before the page is painted: the scheme the operator chose, if any, is set on <html>
// so the first paint is already right. Without a choice the page follows the system.
try {
  const chosen = localStorage.getItem("paeon-theme");
  if (chosen === "light" || chosen === "dark") document.documentElement.dataset.theme = chosen;
} catch {
  // Storage refused (private mode): follow the system.
}
