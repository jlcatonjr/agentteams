document.addEventListener("DOMContentLoaded", function () {
  var yearNode = document.getElementById("copyright-year");
  if (!yearNode) {
    return;
  }

  yearNode.textContent = String(new Date().getFullYear());
});
