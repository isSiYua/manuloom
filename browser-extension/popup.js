const endpointInput = document.getElementById("endpoint");
const tokenInput = document.getElementById("token");
const submitButton = document.getElementById("submit");
const statusBox = document.getElementById("status");
let activeTab = null;

function normalizedEndpoint(value) {
  const endpoint = new URL(value);
  if (endpoint.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(endpoint.hostname)) {
    throw new Error("服务地址必须是本机 http://127.0.0.1 或 localhost");
  }
  if (endpoint.port && endpoint.port !== "8766") {
    throw new Error("浏览器扩展当前只连接本机 8766 端口");
  }
  return endpoint.origin;
}

function supportedPage(url) {
  try {
    return ["http:", "https:"].includes(new URL(url).protocol);
  } catch (_) {
    return false;
  }
}

function showStatus(message, failed = false) {
  statusBox.textContent = message;
  statusBox.style.color = failed ? "#ff9b9b" : "#b8cce0";
}

async function poll(endpoint, token, id) {
  const response = await fetch(`${endpoint}/api/jobs/${id}`, {
    headers: {Authorization: `Bearer ${token}`}
  });
  const job = await response.json();
  if (!response.ok) throw new Error(job.error || "无法读取任务状态");
  if (["queued", "running"].includes(job.status)) {
    showStatus(`任务 ${id}：${job.status === "queued" ? "等待中" : "正在生成"}。关闭窗口不会中断任务。`);
    setTimeout(() => poll(endpoint, token, id).catch(error => showStatus(error.message, true)), 2500);
    return;
  }
  submitButton.disabled = false;
  await chrome.storage.local.remove("lastJobId");
  showStatus(job.status === "complete" ? `完成：${job.result?.note || "请到 Obsidian Vault 查看"}` : `失败：${job.error || "未知错误"}`, job.status !== "complete");
}

async function initialize() {
  const stored = await chrome.storage.local.get(["endpoint", "bridgeToken", "lastJobId"]);
  endpointInput.value = stored.endpoint || endpointInput.value;
  tokenInput.value = stored.bridgeToken || "";
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  activeTab = tab;
  document.getElementById("title").textContent = tab?.title || "未读取到标题";
  document.getElementById("url").textContent = tab?.url || "";
  if (!supportedPage(tab?.url || "")) {
    submitButton.disabled = true;
    showStatus("请打开一个公开的 http/https 视频或文章页面。", true);
  }
  if (stored.lastJobId && tokenInput.value) {
    const endpoint = normalizedEndpoint(endpointInput.value.trim());
    submitButton.disabled = true;
    poll(endpoint, tokenInput.value, stored.lastJobId).catch(async error => {
      await chrome.storage.local.remove("lastJobId");
      submitButton.disabled = false;
      showStatus(error.message, true);
    });
  }
}

submitButton.addEventListener("click", async () => {
  submitButton.disabled = true;
  try {
    const endpoint = normalizedEndpoint(endpointInput.value.trim());
    const token = tokenInput.value.trim();
    if (token.length < 24) throw new Error("请先粘贴本机配对令牌");
    if (!supportedPage(activeTab?.url || "")) throw new Error("当前页面不是可提交的网页");
    await chrome.storage.local.set({endpoint, bridgeToken: token});
    showStatus("正在提交当前页面…");
    const response = await fetch(`${endpoint}/api/jobs`, {
      method: "POST",
      headers: {"Content-Type": "application/json", Authorization: `Bearer ${token}`},
      body: JSON.stringify({url: activeTab.url, title: activeTab.title || ""})
    });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "提交失败");
    await chrome.storage.local.set({lastJobId: job.id});
    showStatus(`已提交任务 ${job.id}`);
    poll(endpoint, token, job.id).catch(error => showStatus(error.message, true));
  } catch (error) {
    submitButton.disabled = false;
    showStatus(error.message, true);
  }
});

initialize().catch(error => showStatus(error.message, true));
