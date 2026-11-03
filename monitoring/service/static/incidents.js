"use strict";

const form = document.querySelector("#incident-selection");
if (form) {
  const rows = Array.from(form.querySelectorAll('input[name="selection"]'));
  const selectAll = form.querySelector("[data-select-all]");
  const count = form.querySelector("[data-selection-count]");
  const actions = Array.from(form.querySelectorAll('button[name="status"]'));

  function update() {
    const selected = rows.filter((row) => row.checked).length;
    count.textContent = `${selected} selected`;
    selectAll.checked = rows.length > 0 && selected === rows.length;
    selectAll.indeterminate = selected > 0 && selected < rows.length;
    actions.forEach((button) => { button.disabled = selected === 0; });
  }

  selectAll.closest("label").hidden = false;
  selectAll.addEventListener("change", () => {
    rows.forEach((row) => { row.checked = selectAll.checked; });
    update();
  });
  form.addEventListener("change", update);
  window.addEventListener("pageshow", update);
  update();
}
