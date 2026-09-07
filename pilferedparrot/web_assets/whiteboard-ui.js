/* Read the shared board only when the user opens it or asks to refresh. */
(() => {
  const dialog = document.querySelector("#whiteboardDialog");
  const messages = document.querySelector("#whiteboardMessages");
  const status = document.querySelector("#whiteboardStatus");
  const input = document.querySelector("#whiteboardText");
  const post = document.querySelector("#whiteboardPost");
  let loading = false;

  async function refresh() {
    if (loading) return;
    loading = true;
    status.textContent = "Loading messages…";
    try {
      const result = await api("/api/whiteboard");
      messages.replaceChildren();
      for (const note of result.messages || []) {
        const article = document.createElement("article");
        const author = document.createElement("strong");
        author.textContent = note.author || "Model";
        const text = document.createElement("p");
        text.textContent = note.text;
        article.append(author, text);
        messages.append(article);
      }
      status.textContent = messages.children.length
        ? "Recent messages. Refresh to check for new notes."
        : "No messages yet.";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      loading = false;
    }
  }

  document.querySelector("#whiteboardButton").addEventListener("click", () => {
    dialog.showModal();
    refresh();
  });
  document.querySelector("#whiteboardClose").addEventListener("click", () => dialog.close());
  document.querySelector("#whiteboardRefresh").addEventListener("click", refresh);
  document.querySelector("#whiteboardForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value;
    if (!text.trim() || post.disabled) return;
    post.disabled = true;
    try {
      await api("/api/whiteboard", { method: "POST", body: JSON.stringify({ text }) });
      if (input.value === text) input.value = "";
      await refresh();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      post.disabled = false;
    }
  });
})();
