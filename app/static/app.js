const state = {
  documents: [],
  filteredDocuments: [],
  stats: null,
  sessionId: null,
  activeDocumentId: null,
  activeDocumentTitle: null,
  chatMessages: [],
  previewDocument: null,
  pollingTimer: null,
  activeView: "chat",
};

const supportedFormatsText = "Поддерживаются: PDF, DOCX, DOC, TXT, CSV, RTF, XLSX, XLS, PNG, JPG, JPEG, WEBP, TIF, TIFF, BMP.";

const elements = {
  totalDocuments: document.getElementById("totalDocuments"),
  activeDocuments: document.getElementById("activeDocuments"),
  expiringDocuments: document.getElementById("expiringDocuments"),
  expiredDocuments: document.getElementById("expiredDocuments"),
  openDocumentsPanelButton: document.getElementById("openDocumentsPanelButton"),
  openChatPanelButton: document.getElementById("openChatPanelButton"),
  documentsPanel: document.getElementById("documentsPanel"),
  chatPanel: document.getElementById("chatPanel"),
  documentsSearch: document.getElementById("documentsSearch"),
  reloadDocumentsButton: document.getElementById("reloadDocumentsButton"),
  documentsTableBody: document.getElementById("documentsTableBody"),
  openUploadButton: document.getElementById("openUploadButton"),
  openUploadButtonInDocuments: document.getElementById("openUploadButtonInDocuments"),
  uploadDialog: document.getElementById("uploadDialog"),
  uploadForm: document.getElementById("uploadForm"),
  closeUploadButton: document.getElementById("closeUploadButton"),
  cancelUploadButton: document.getElementById("cancelUploadButton"),
  submitUploadButton: document.getElementById("submitUploadButton"),
  previewDialog: document.getElementById("previewDialog"),
  closePreviewButton: document.getElementById("closePreviewButton"),
  previewTitle: document.getElementById("previewTitle"),
  previewMetaLine: document.getElementById("previewMetaLine"),
  previewMetadata: document.getElementById("previewMetadata"),
  previewSummary: document.getElementById("previewSummary"),
  previewProcessingError: document.getElementById("previewProcessingError"),
  previewBlocks: document.getElementById("previewBlocks"),
  openFileButton: document.getElementById("openFileButton"),
  sendPreviewToChatButton: document.getElementById("sendPreviewToChatButton"),
  editMetadataButton: document.getElementById("editMetadataButton"),
  editDialog: document.getElementById("editDialog"),
  editForm: document.getElementById("editForm"),
  closeEditButton: document.getElementById("closeEditButton"),
  cancelEditButton: document.getElementById("cancelEditButton"),
  submitEditButton: document.getElementById("submitEditButton"),
  editDocumentId: document.getElementById("editDocumentId"),
  chatContextBadge: document.getElementById("chatContextBadge"),
  clearContextButton: document.getElementById("clearContextButton"),
  refreshChatButton: document.getElementById("refreshChatButton"),
  chatLog: document.getElementById("chatLog"),
  quickActions: document.getElementById("quickActions"),
  chatForm: document.getElementById("chatForm"),
  chatQuestion: document.getElementById("chatQuestion"),
  sendQuestionButton: document.getElementById("sendQuestionButton"),
  assistantTemplate: document.getElementById("assistantMessageTemplate"),
  userTemplate: document.getElementById("userMessageTemplate"),
};

function getApiUrl(path) {
  return path.startsWith("/api") ? path : `/api${path}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatDocumentType(value) {
  const map = {
    contract: "Договор",
    license: "Лицензия",
    agreement: "Соглашение",
    act: "Акт",
    appendix: "Приложение",
    invoice: "Счет",
    scan: "Скан",
    document: "Документ",
  };
  return map[String(value || "").toLowerCase()] || "Документ";
}

function formatDate(value) {
  if (!value || value === "-" || value === "срок не указан") {
    return "срок не указан";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return new Intl.DateTimeFormat("ru-RU").format(date);
}

function formatAmount(value, currency = "RUB", emptyText = "не указана") {
  if (value === null || value === undefined || value === "" || value === "-" || Number.isNaN(Number(value))) {
    return emptyText;
  }
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount <= 0) {
    return emptyText;
  }

  const currencyMap = { RUB: "₽", RUR: "₽", USD: "$", EUR: "€" };
  const symbol = currencyMap[String(currency || "RUB").toUpperCase()] || currency || "₽";
  const formatted = new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(amount);
  return `${formatted} ${symbol}`;
}

function getDocumentStatusText(documentRecord) {
  if (documentRecord.processingStatus === "failed") return "Ошибка";
  if (documentRecord.processingStatus === "processing") return "Обработка";
  const map = {
    active: "Активен",
    expiring: "Истекает скоро",
    expired: "Просрочен",
    no_date: "Срок не указан",
  };
  return map[String(documentRecord.businessStatus || "").toLowerCase()] || "Срок не указан";
}

function getStatusClass(statusText) {
  const normalized = String(statusText || "").toLowerCase();
  if (normalized.includes("актив")) return "status-active";
  if (normalized.includes("истека")) return "status-expiring";
  if (normalized.includes("просроч")) return "status-expired";
  if (normalized.includes("ошиб")) return "status-failed";
  if (normalized.includes("обработ")) return "status-processing";
  return "status-no-date";
}

function showNotice(target, message, kind = "error") {
  if (!target) return;
  const notice = document.createElement("div");
  notice.className = `notice notice-${kind}`;
  notice.textContent = message;
  target.prepend(notice);
  setTimeout(() => notice.remove(), 5000);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (error) {
      // ignore
    }
    throw new Error(detail);
  }
  return response.json();
}

function setDocumentsButtonActive(isActive) {
  elements.openDocumentsPanelButton.classList.toggle("is-active", isActive);
  elements.openChatPanelButton.classList.toggle("is-active", !isActive);
}

function switchWorkspaceView(viewName) {
  state.activeView = viewName === "documents" ? "documents" : "chat";
  const isDocumentsView = state.activeView === "documents";
  setDocumentsButtonActive(isDocumentsView);
  elements.documentsPanel.classList.toggle("hidden", !isDocumentsView);
  elements.chatPanel.classList.toggle("hidden", isDocumentsView);
}

function getDocumentsNoticeTarget() {
  return state.activeView === "documents"
    ? (elements.documentsPanel || document.querySelector(".main-panel"))
    : (elements.chatPanel || document.querySelector(".main-panel"));
}

function getChatNoticeTarget() {
  return elements.chatPanel || document.querySelector(".main-panel");
}

function openUploadDialog() {
  elements.uploadForm.reset();
  elements.uploadDialog.showModal();
}

function closeUploadDialog() {
  elements.uploadDialog.close();
}

function closePreviewDialog() {
  elements.previewDialog.close();
}

function closeEditDialog() {
  elements.editDialog.close();
}

async function loadDashboard({ silent = false } = {}) {
  try {
    const [documents, stats] = await Promise.all([
      fetchJson(getApiUrl("/documents")),
      fetchJson(getApiUrl("/documents/stats")),
    ]);
    state.documents = documents;
    applyDocumentsFilter();
    state.stats = stats;
    renderStats();
    renderDocuments();
    syncPollingState();
  } catch (error) {
    if (!silent) {
      showNotice(getDocumentsNoticeTarget(), `Не удалось загрузить документы: ${error.message}`);
    }
  }
}

function renderStats() {
  const stats = state.stats || { total: 0, active: 0, expiring: 0, expired: 0 };
  elements.totalDocuments.textContent = stats.total ?? 0;
  elements.activeDocuments.textContent = stats.active ?? 0;
  elements.expiringDocuments.textContent = stats.expiring ?? 0;
  elements.expiredDocuments.textContent = stats.expired ?? 0;
}

function applyDocumentsFilter() {
  const query = elements.documentsSearch.value.trim().toLowerCase();
  if (!query) {
    state.filteredDocuments = [...state.documents];
    return;
  }

  state.filteredDocuments = state.documents.filter((documentRecord) =>
    [
      documentRecord.title,
      documentRecord.fileName,
      documentRecord.vendor,
      documentRecord.documentType,
      documentRecord.contractNumber,
      documentRecord.softwareName,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query))
  );
}

function renderDocuments() {
  const tbody = elements.documentsTableBody;
  tbody.innerHTML = "";

  if (!state.filteredDocuments.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state-cell">Документы по текущему фильтру не найдены.</td></tr>`;
    return;
  }

  for (const documentRecord of state.filteredDocuments) {
    const statusText = getDocumentStatusText(documentRecord);
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>
        <strong>${escapeHtml(documentRecord.title)}</strong>
        <small>${escapeHtml(documentRecord.fileName || documentRecord.originalFileName || "")}</small>
      </td>
      <td>${escapeHtml(formatDocumentType(documentRecord.documentType))}</td>
      <td>${escapeHtml(documentRecord.vendor || "-")}</td>
      <td>${documentRecord.validTo ? escapeHtml(formatDate(documentRecord.validTo)) : "-"}</td>
      <td>${escapeHtml(formatAmount(documentRecord.amount, documentRecord.currency, "-"))}</td>
      <td>
        <span class="status-pill ${getStatusClass(statusText)}">${escapeHtml(statusText)}</span>
        ${documentRecord.processingError ? `<br><small>${escapeHtml(documentRecord.processingError)}</small>` : ""}
      </td>
      <td>
        <div class="action-group">
          <button type="button" data-action="preview">Просмотр</button>
          <button type="button" data-action="edit">Редактировать</button>
          <button type="button" data-action="chat">В чат</button>
          <button type="button" data-action="reindex">Переиндексировать</button>
          <button type="button" data-action="delete">Удалить</button>
        </div>
      </td>
    `;

    row.querySelector('[data-action="preview"]').addEventListener("click", () => openPreview(documentRecord.id));
    row.querySelector('[data-action="edit"]').addEventListener("click", () => openEditDialog(documentRecord.id));
    row.querySelector('[data-action="chat"]').addEventListener("click", () => sendDocumentToChatContext(documentRecord));
    row.querySelector('[data-action="reindex"]').addEventListener("click", () => reindexDocument(documentRecord.id));
    row.querySelector('[data-action="delete"]').addEventListener("click", () => deleteDocument(documentRecord.id));

    tbody.appendChild(row);
  }
}

function syncPollingState() {
  const hasProcessing = state.documents.some((documentRecord) => documentRecord.processingStatus === "processing");
  if (hasProcessing && !state.pollingTimer) {
    state.pollingTimer = setInterval(() => loadDashboard({ silent: true }), 3000);
  }
  if (!hasProcessing && state.pollingTimer) {
    clearInterval(state.pollingTimer);
    state.pollingTimer = null;
  }
}

async function submitUpload(event) {
  event.preventDefault();
  const file = document.getElementById("uploadFile").files[0];
  if (!file) {
    showNotice(elements.uploadForm, "Выберите файл для загрузки.");
    return;
  }

  const formData = new FormData(elements.uploadForm);
  elements.submitUploadButton.disabled = true;

  try {
    const payload = await fetchJson(getApiUrl("/documents/upload"), {
      method: "POST",
      body: formData,
    });
    closeUploadDialog();
    await loadDashboard({ silent: true });
    const target = getDocumentsNoticeTarget();
    if (payload.processingStatus === "processing") {
      showNotice(target, `Документ «${payload.title}» загружен. Индексация выполняется в фоне.`, "success");
    } else {
      showNotice(target, `Документ «${payload.title}» загружен и готов к работе.`, "success");
    }
  } catch (error) {
    let message = `Не удалось загрузить документ: ${error.message}`;
    if (String(error.message || "").includes("Неподдерживаемый тип файла")) {
      message = `${message}. ${supportedFormatsText}`;
    }
    showNotice(elements.uploadForm, message);
  } finally {
    elements.submitUploadButton.disabled = false;
  }
}

async function openPreview(documentId) {
  try {
    const [preview, documentRecord] = await Promise.all([
      fetchJson(getApiUrl(`/documents/${documentId}/preview`)),
      fetchJson(getApiUrl(`/documents/${documentId}`)),
    ]);
    state.previewDocument = { ...documentRecord, preview };
    elements.previewTitle.textContent = preview.title;
    elements.previewMetaLine.textContent = `${preview.documentType} • ${preview.businessStatus} • ${preview.processingStatus || "indexed"}`;
    elements.previewSummary.textContent = preview.shortSummary || "Краткое содержание пока не сформировано.";
    elements.previewProcessingError.textContent = preview.processingError || "";
    elements.previewProcessingError.classList.toggle("hidden", !preview.processingError);
    elements.openFileButton.href = preview.fileUrl;
    renderPreviewMetadata(documentRecord, preview);
    renderPreviewBlocks(preview.blocks || []);
    elements.previewDialog.showModal();
  } catch (error) {
    showNotice(getDocumentsNoticeTarget(), `Не удалось открыть документ: ${error.message}`);
  }
}

function renderPreviewMetadata(documentRecord, preview) {
  const items = [
    ["Тип", formatDocumentType(documentRecord.documentType)],
    ["Поставщик", documentRecord.vendor || "-"],
    ["Номер договора", documentRecord.contractNumber || "-"],
    ["Дата начала", documentRecord.validFrom ? formatDate(documentRecord.validFrom) : "-"],
    ["Дата окончания", documentRecord.validTo ? formatDate(documentRecord.validTo) : "-"],
    ["Сумма", formatAmount(documentRecord.amount, documentRecord.currency, "-")],
    ["Программный продукт", documentRecord.softwareName || "-"],
    ["Количество лицензий", documentRecord.licenseCount ?? "-"],
    ["Статус", getDocumentStatusText(documentRecord)],
    ["Индексация", preview.processingStatus || "-"],
  ];

  elements.previewMetadata.innerHTML = items
    .map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`)
    .join("");
}

function renderPreviewBlocks(blocks) {
  if (!blocks.length) {
    elements.previewBlocks.innerHTML = `<article class="block-card"><p class="block-text">Документ еще обрабатывается или извлеченный текст пока отсутствует.</p></article>`;
    return;
  }

  elements.previewBlocks.innerHTML = blocks.map((block) => {
    const warnings = Array.isArray(block.warnings) && block.warnings.length
      ? `<p class="block-text"><strong>Предупреждения:</strong> ${escapeHtml(block.warnings.join("; "))}</p>`
      : "";
    return `
      <article class="block-card">
        <div class="block-header">
          <span>${escapeHtml(block.sourceType || "text")} / ${escapeHtml(block.extractionMethod || "unknown")}</span>
          <span>${block.pageNumber ? `стр. ${escapeHtml(block.pageNumber)}` : "без номера страницы"}</span>
        </div>
        <p class="block-text">${escapeHtml(block.text || "")}</p>
        ${warnings}
      </article>
    `;
  }).join("");
}

async function openEditDialog(documentId) {
  try {
    const documentRecord = await fetchJson(getApiUrl(`/documents/${documentId}`));
    elements.editDocumentId.value = documentRecord.id;
    document.getElementById("editTitle").value = documentRecord.title || "";
    document.getElementById("editDocumentType").value = documentRecord.documentType || "document";
    document.getElementById("editVendor").value = documentRecord.vendor || "";
    document.getElementById("editContractNumber").value = documentRecord.contractNumber || "";
    document.getElementById("editValidFrom").value = normalizeDateForInput(documentRecord.validFrom);
    document.getElementById("editValidTo").value = normalizeDateForInput(documentRecord.validTo);
    document.getElementById("editAmount").value = documentRecord.amount ?? "";
    document.getElementById("editCurrency").value = documentRecord.currency || "RUB";
    document.getElementById("editSoftwareName").value = documentRecord.softwareName || "";
    document.getElementById("editLicenseCount").value = documentRecord.licenseCount ?? "";
    document.getElementById("editComment").value = documentRecord.comment || "";
    elements.editDialog.showModal();
  } catch (error) {
    showNotice(getDocumentsNoticeTarget(), `Не удалось открыть форму редактирования: ${error.message}`);
  }
}

function normalizeDateForInput(value) {
  if (!value) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) {
    return String(value);
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

async function submitEdit(event) {
  event.preventDefault();
  const documentId = elements.editDocumentId.value;
  const formData = new FormData(elements.editForm);
  elements.submitEditButton.disabled = true;

  try {
    await fetchJson(getApiUrl(`/documents/${documentId}`), {
      method: "PATCH",
      body: formData,
    });
    closeEditDialog();
    await loadDashboard({ silent: true });
    if (state.previewDocument && state.previewDocument.id === documentId) {
      await openPreview(documentId);
    }
    showNotice(getDocumentsNoticeTarget(), "Метаданные документа обновлены.", "success");
  } catch (error) {
    showNotice(elements.editForm, `Не удалось сохранить изменения: ${error.message}`);
  } finally {
    elements.submitEditButton.disabled = false;
  }
}

function sendDocumentToChatContext(documentRecord) {
  state.activeDocumentId = documentRecord.id;
  state.activeDocumentTitle = documentRecord.title;
  updateChatContextBadge();
  switchWorkspaceView("chat");
  elements.chatQuestion.focus();
  showNotice(getChatNoticeTarget(), `Документ «${documentRecord.title}» выбран для вопросов в чате.`, "success");
}

function updateChatContextBadge() {
  elements.chatContextBadge.textContent = state.activeDocumentTitle
    ? `Контекст: ${state.activeDocumentTitle}`
    : "Контекст: все документы";
}

function clearChatContext() {
  state.activeDocumentId = null;
  state.activeDocumentTitle = null;
  updateChatContextBadge();
}

async function deleteDocument(documentId) {
  if (!window.confirm("Удалить документ?")) {
    return;
  }
  try {
    await fetchJson(getApiUrl(`/documents/${documentId}`), { method: "DELETE" });
    if (state.activeDocumentId === documentId) {
      clearChatContext();
    }
    await loadDashboard({ silent: true });
    if (state.previewDocument && state.previewDocument.id === documentId) {
      closePreviewDialog();
      state.previewDocument = null;
    }
    showNotice(getDocumentsNoticeTarget(), "Документ удален.", "success");
  } catch (error) {
    showNotice(getDocumentsNoticeTarget(), `Не удалось удалить документ: ${error.message}`);
  }
}

async function reindexDocument(documentId) {
  try {
    const payload = await fetchJson(getApiUrl(`/documents/${documentId}/reindex`), { method: "POST" });
    await loadDashboard({ silent: true });
    showNotice(getDocumentsNoticeTarget(), `Документ «${payload.title}» отправлен на переиндексацию.`, "success");
  } catch (error) {
    showNotice(getDocumentsNoticeTarget(), `Не удалось переиндексировать документ: ${error.message}`);
  }
}

function renderChat() {
  elements.chatLog.innerHTML = "";
  if (!state.chatMessages.length) {
    elements.chatLog.innerHTML = `<div class="chat-empty">История пуста. Загрузите документы и задайте первый вопрос.</div>`;
    return;
  }

  for (const message of state.chatMessages) {
    const template = message.role === "user" ? elements.userTemplate : elements.assistantTemplate;
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector(".message-body").textContent = message.content;

    if (message.role === "assistant") {
      node.querySelector(".message-source").textContent = buildSourceLabel(message.payload);
      renderAssistantDetails(node, message.payload);
    }
    elements.chatLog.appendChild(node);
  }

  elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
}

function buildSourceLabel(payload) {
  if (!payload) return "";
  const map = {
    document_metadata: "metadata документов",
    document_collection_metadata: "обзор документов",
    document_rag: "RAG / OCR",
    document_text: "полный текст документа",
  };
  return `Источник: ${map[payload.sourceType] || payload.sourceType || "RAG"}`;
}

function renderAssistantDetails(node, payload) {
  const details = node.querySelector(".message-details");
  const detailsContent = node.querySelector(".details-content");
  if (!payload) {
    details.classList.add("hidden");
    return;
  }

  const cards = [];

  if (Array.isArray(payload.documentsOverview) && payload.documentsOverview.length) {
    cards.push(`
      <section class="details-card">
        <h5>Документы</h5>
        <ul>
          ${payload.documentsOverview.map((item) => `
            <li>
              <strong>${escapeHtml(item.title)}</strong><br>
              ${escapeHtml(formatDocumentType(item.documentType))},
              ${escapeHtml(formatAmount(item.amount, item.currency, "сумма не указана"))},
              ${escapeHtml(item.shortSummary || "")}<br>
              <button class="ghost-button details-open-document" type="button" data-document-id="${escapeHtml(item.documentId)}">Открыть документ</button>
            </li>
          `).join("")}
        </ul>
      </section>
    `);
  }

  if (Array.isArray(payload.includedDocuments) && payload.includedDocuments.length) {
    cards.push(`
      <section class="details-card">
        <h5>Включены в расчет</h5>
        <ul>
          ${payload.includedDocuments.map((item) => `
            <li>
              ${escapeHtml(item.title)} — ${escapeHtml(formatAmount(item.amount, payload.currency || "RUB"))}
              <button class="ghost-button details-open-document" type="button" data-document-id="${escapeHtml(item.documentId)}">Открыть документ</button>
            </li>
          `).join("")}
        </ul>
      </section>
    `);
  }

  if (Array.isArray(payload.excludedDocuments) && payload.excludedDocuments.length) {
    cards.push(`
      <section class="details-card">
        <h5>Не включены в расчет</h5>
        <ul>
          ${payload.excludedDocuments.map((item) => `
            <li>
              ${escapeHtml(item.title)}
              ${item.documentId ? `<button class="ghost-button details-open-document" type="button" data-document-id="${escapeHtml(item.documentId)}">Открыть документ</button>` : ""}
            </li>
          `).join("")}
        </ul>
      </section>
    `);
  }

  if (Array.isArray(payload.citations) && payload.citations.length) {
    cards.push(`
      <section class="details-card">
        <h5>Источники</h5>
        <ul>
          ${payload.citations.map((citation) => `
            <li>
              <strong>${escapeHtml(citation.documentTitle || "Документ")}</strong>
              ${citation.pageNumber ? `, стр. ${escapeHtml(citation.pageNumber)}` : ""}
              ${citation.sourceType ? `, ${escapeHtml(citation.sourceType)}` : ""}<br>
              ${escapeHtml(citation.quote || "")}<br>
              ${citation.documentId ? `<button class="ghost-button details-open-document" type="button" data-document-id="${escapeHtml(citation.documentId)}">Открыть документ</button>` : ""}
            </li>
          `).join("")}
        </ul>
      </section>
    `);
  }

  if (!cards.length) {
    details.classList.add("hidden");
    return;
  }

  details.classList.remove("hidden");
  detailsContent.innerHTML = cards.join("");
}

async function submitQuestion(event) {
  event.preventDefault();
  const question = elements.chatQuestion.value.trim();
  if (!question) {
    return;
  }

  state.chatMessages.push({ role: "user", content: question });
  renderChat();
  elements.chatQuestion.value = "";
  elements.sendQuestionButton.disabled = true;

  try {
    const payload = await fetchJson(getApiUrl("/rag/ask"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        sessionId: state.sessionId,
        documentId: state.activeDocumentId,
      }),
    });

    state.sessionId = payload.sessionId || state.sessionId;
    state.chatMessages.push({ role: "assistant", content: payload.answer, payload });
  } catch (error) {
    state.chatMessages.push({
      role: "assistant",
      content: `Не удалось получить ответ от RAG-модуля: ${error.message}`,
      payload: { sourceType: "error", citations: [] },
    });
  } finally {
    elements.sendQuestionButton.disabled = false;
    renderChat();
  }
}

async function loadExistingChatSession() {
  if (!state.sessionId) {
    state.chatMessages = [];
    renderChat();
    return;
  }
  try {
    const payload = await fetchJson(getApiUrl(`/rag/chat/sessions/${state.sessionId}/messages`));
    state.chatMessages = (payload.messages || []).map((message) => ({
      role: message.role,
      content: message.content,
      payload: message.metadata?.response || null,
    }));
  } catch (error) {
    state.chatMessages = [];
  }
  renderChat();
}

function bindQuickActions() {
  elements.quickActions.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-question]");
    if (!button) return;
    elements.chatQuestion.value = button.dataset.question || "";
    elements.chatForm.requestSubmit();
  });
}

function bindChatDetailsActions() {
  elements.chatLog.addEventListener("click", (event) => {
    const openButton = event.target.closest(".details-open-document");
    if (!openButton) return;
    const documentId = openButton.dataset.documentId;
    if (documentId) {
      openPreview(documentId);
    }
  });
}

function wireEvents() {
  elements.openDocumentsPanelButton.addEventListener("click", () => switchWorkspaceView("documents"));
  elements.openChatPanelButton.addEventListener("click", () => switchWorkspaceView("chat"));
  elements.documentsSearch.addEventListener("input", () => {
    applyDocumentsFilter();
    renderDocuments();
  });
  elements.reloadDocumentsButton.addEventListener("click", () => loadDashboard());
  elements.openUploadButton.addEventListener("click", openUploadDialog);
  elements.openUploadButtonInDocuments.addEventListener("click", openUploadDialog);
  elements.closeUploadButton.addEventListener("click", closeUploadDialog);
  elements.cancelUploadButton.addEventListener("click", closeUploadDialog);
  elements.uploadForm.addEventListener("submit", submitUpload);
  elements.closePreviewButton.addEventListener("click", closePreviewDialog);
  elements.sendPreviewToChatButton.addEventListener("click", () => {
    if (state.previewDocument) {
      sendDocumentToChatContext(state.previewDocument);
      closePreviewDialog();
    }
  });
  elements.editMetadataButton.addEventListener("click", () => {
    if (state.previewDocument) {
      openEditDialog(state.previewDocument.id);
    }
  });
  elements.closeEditButton.addEventListener("click", closeEditDialog);
  elements.cancelEditButton.addEventListener("click", closeEditDialog);
  elements.editForm.addEventListener("submit", submitEdit);
  elements.clearContextButton.addEventListener("click", clearChatContext);
  elements.refreshChatButton.addEventListener("click", loadExistingChatSession);
  elements.chatForm.addEventListener("submit", submitQuestion);
  bindQuickActions();
  bindChatDetailsActions();
}

async function init() {
  try {
    localStorage.removeItem("ragSessionId");
  } catch (error) {
    // ignore localStorage errors
  }
  wireEvents();
  updateChatContextBadge();
  switchWorkspaceView("chat");
  await loadDashboard();
  renderChat();
}

init();
