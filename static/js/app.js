document.addEventListener("DOMContentLoaded", function () {
  const form = document.querySelector("form");
  const pastedText = document.querySelector('textarea[name="pasted_text"]');
  const fileInput = document.querySelector('input[type="file"][name="files"]');
  const submitBtn = form ? form.querySelector("button[type='submit']") : null;

  // --- Live word count for text box mode ---
  if (pastedText) {
    const counter = document.createElement("div");
    counter.className = "text-muted";
    counter.style.marginTop = "0.25rem";
    pastedText.parentNode.appendChild(counter);

    const updateCount = () => {
      const text = pastedText.value.trim();
      const words = text ? text.split(/\s+/).length : 0;
      counter.textContent = words + " word" + (words === 1 ? "" : "s");
    };

    pastedText.addEventListener("input", updateCount);
    updateCount();
  }

  // --- Show list of selected files ---
  if (fileInput) {
    const fileList = document.createElement("div");
    fileList.className = "file-list";
    fileInput.parentNode.appendChild(fileList);

    fileInput.addEventListener("change", () => {
      if (!fileInput.files || fileInput.files.length === 0) {
        fileList.textContent = "";
        return;
      }
      const names = Array.from(fileInput.files).map((f) => f.name);
      fileList.textContent = "Selected: " + names.join(", ");
    });
  }

  // --- Disable submit button on submit + simple validation hint ---
  if (form && submitBtn) {
    form.addEventListener("submit", (e) => {
      // Basic check: if no text and no files, block submit
      const hasText =
        pastedText && pastedText.value.trim().length > 0;
      const hasFiles =
        fileInput && fileInput.files && fileInput.files.length > 0;

      if (!hasText && !hasFiles) {
        e.preventDefault();
        alert("Please paste text or upload at least one file.");
        return;
      }

      submitBtn.disabled = true;
      submitBtn.dataset.originalText = submitBtn.textContent;
      submitBtn.textContent = "Processing...";
    });
  }
});
