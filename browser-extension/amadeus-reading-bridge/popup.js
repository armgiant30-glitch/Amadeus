const status = document.getElementById("status");
const button = document.getElementById("send");

function render(result) {
  if (result?.ok) {
    status.textContent = `已发送 ${result.chars} 个字符`;
    status.className = "ok";
  } else {
    status.textContent = result?.error || "发送失败";
    status.className = "error";
  }
}

button.addEventListener("click", async () => {
  status.textContent = "正在发送…";
  status.className = "";
  const result = await chrome.runtime.sendMessage({ type: "AMADEUS_SEND_ACTIVE_SELECTION" });
  render(result);
});

chrome.storage.local.get("lastReadingStatus").then(({ lastReadingStatus }) => {
  if (lastReadingStatus) render(lastReadingStatus);
});
