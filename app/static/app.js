"use strict";
document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const customize = document.querySelector("#customize-captions");
const updateVariantPanels = () => {
  document.querySelectorAll("[data-platform-panel]").forEach((panel) => {
    const checkbox = document.querySelector(`input[name="platforms"][value="${panel.dataset.platformPanel}"]`);
    panel.hidden = !checkbox?.checked;
    const caption = panel.querySelector(".caption-override");
    if (caption) caption.hidden = !customize?.checked;
  });
};
document.querySelectorAll('input[name="platforms"]').forEach((input) => input.addEventListener("change", updateVariantPanels));
customize?.addEventListener("change", updateVariantPanels);
updateVariantPanels();

document.querySelectorAll('[data-sortable="true"]').forEach((list) => {
  let dragged = null;
  list.addEventListener("dragstart", (event) => {
    dragged = event.target.closest("[data-media-id]");
    dragged?.classList.add("dragging");
  });
  list.addEventListener("dragover", (event) => {
    event.preventDefault();
    const target = event.target.closest("[data-media-id]");
    if (dragged && target && dragged !== target) {
      const box = target.getBoundingClientRect();
      list.insertBefore(dragged, event.clientY < box.top + box.height / 2 ? target : target.nextSibling);
    }
  });
  list.addEventListener("dragend", () => {
    dragged?.classList.remove("dragging");
    dragged = null;
    [...list.querySelectorAll("[data-media-id]")].forEach((card, index) => {
      card.querySelector(".media-order").textContent = index + 1;
    });
    const form = list.closest(".media-group").querySelector(".reorder-form");
    if (form) form.querySelector(".ordered-ids").value = [...list.querySelectorAll("[data-media-id]")].map((item) => item.dataset.mediaId).join(",");
  });
});
