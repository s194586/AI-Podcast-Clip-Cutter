const els = {
  projectSelector: document.querySelector("#projectSelector"),
  refreshProjectsButton: document.querySelector("#refreshProjectsButton"),
  projectUrlInput: document.querySelector("#projectUrlInput"),
  projectTitleInput: document.querySelector("#projectTitleInput"),
  projectAutoReview: document.querySelector("#projectAutoReview"),
  createProjectButton: document.querySelector("#createProjectButton"),
  startProjectButton: document.querySelector("#startProjectButton"),
  cancelProjectButton: document.querySelector("#cancelProjectButton"),
  projectProgress: document.querySelector("#projectProgress"),
  projectFlowStatus: document.querySelector("#projectFlowStatus"),
  openProjectClipsButton: document.querySelector("#openProjectClipsButton"),
  projectLogTail: document.querySelector("#projectLogTail"),
  clipList: document.querySelector("#clipList"),
  clipCount: document.querySelector("#clipCount"),
  sourceStatus: document.querySelector("#sourceStatus"),
  sourceWarning: document.querySelector("#sourceWarning"),
  reviewAllButton: document.querySelector("#reviewAllButton"),
  configuredReviewProvider: document.querySelector("#configuredReviewProvider"),
  lastReviewProvider: document.querySelector("#lastReviewProvider"),
  aiReviewStatus: document.querySelector("#aiReviewStatus"),
  previewVideo: document.querySelector("#previewVideo"),
  emptyPreview: document.querySelector("#emptyPreview"),
  currentTime: document.querySelector("#currentTime"),
  editedStart: document.querySelector("#editedStart"),
  editedEnd: document.querySelector("#editedEnd"),
  editedDuration: document.querySelector("#editedDuration"),
  timelineRange: document.querySelector("#timelineRange"),
  playhead: document.querySelector("#playhead"),
  previewTime: document.querySelector("#previewTime"),
  startSlider: document.querySelector("#startSlider"),
  endSlider: document.querySelector("#endSlider"),
  playPause: document.querySelector("#playPause"),
  jumpStart: document.querySelector("#jumpStart"),
  loopToggle: document.querySelector("#loopToggle"),
  acceptButton: document.querySelector("#acceptButton"),
  rejectButton: document.querySelector("#rejectButton"),
  startInput: document.querySelector("#startInput"),
  endInput: document.querySelector("#endInput"),
  renderButton: document.querySelector("#renderButton"),
  validationMessage: document.querySelector("#validationMessage"),
  selectedTitle: document.querySelector("#selectedTitle"),
  selectedStatus: document.querySelector("#selectedStatus"),
  selectedSummary: document.querySelector("#selectedSummary"),
  scoreDetails: document.querySelector("#scoreDetails"),
  selectedScore: document.querySelector("#selectedScore"),
  selectedReasons: document.querySelector("#selectedReasons"),
  transcriptDetails: document.querySelector("#transcriptDetails"),
  selectedTranscript: document.querySelector("#selectedTranscript"),
  aiReviewPanel: document.querySelector("#aiReviewPanel"),
  aiReviewDetails: document.querySelector("#aiReviewDetails"),
  renderResult: document.querySelector("#renderResult"),
};

const state = {
  clips: [],
  selectedClip: null,
  editedStart: 0,
  editedEnd: 0,
  loopPreview: true,
  sourceVideoAvailable: false,
  activeProjectId: null,
  activeFlowProjectId: null,
  projectPollingTimer: null,
  projects: [],
  configuredReviewProvider: null,
  projectSelectionRequestId: 0,
  clipLoadRequestId: 0,
  sourceVideoUrl: null,
  sourceVideoProjectId: null,
  sourceVideoRequestId: 0,
};

const SELECTED_PROJECT_STORAGE_KEY = "podcast_cutter_selected_project_id";
const seconds = (value) => `${Number(value || 0).toFixed(2)}s`;
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const duration = () => state.editedEnd - state.editedStart;
const hasValue = (value) => value !== null && value !== undefined && value !== "";
const projectBase = () => (state.activeProjectId ? `/projects/${encodeURIComponent(state.activeProjectId)}` : "");
const projectSourceVideoUrl = (projectId) => `/projects/${encodeURIComponent(projectId)}/source-video`;

async function responseErrorMessage(response, fallback) {
  const text = await response.text();
  if (!text) return fallback;
  try {
    const payload = JSON.parse(text);
    const detail = payload.detail || payload.message || fallback;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  } catch (_error) {
    return text;
  }
}

function providerLabel(value) {
  const text = String(value || "").trim();
  if (!text) return "Unknown";
  if (text === "local_stub") return "local_stub";
  if (text === "gemini") return "Gemini";
  return text;
}

function updateConfiguredReviewProvider(config = null) {
  if (!els.configuredReviewProvider) return;
  if (!config) {
    els.configuredReviewProvider.textContent = "Configured reviewer: Unknown";
    return;
  }
  const provider = config.provider || config.mode || "unknown";
  const model = config.model || config.gemini_model || "";
  state.configuredReviewProvider = provider;
  const details = provider === "gemini" && model ? ` (${model})` : "";
  const keyWarning = provider === "gemini" && config.gemini_api_key_configured === false ? " - API key missing" : "";
  els.configuredReviewProvider.textContent = `Configured reviewer: ${providerLabel(provider)}${details}${keyWarning}`;
}

function updateHistoricalReviewProvider(provider = null) {
  if (!els.lastReviewProvider) return;
  if (provider === "updating") {
    els.lastReviewProvider.textContent = "Last saved review: Updating...";
    return;
  }
  const providers = provider
    ? [provider]
    : [...new Set(state.clips.map((clip) => clip.latest_review_provider).filter(Boolean))];
  if (!providers.length) {
    els.lastReviewProvider.textContent = "Last saved review: None";
  } else if (providers.length === 1) {
    els.lastReviewProvider.textContent = `Last saved review: ${providerLabel(providers[0])}`;
  } else {
    els.lastReviewProvider.textContent = `Last saved review: mixed (${providers.map(providerLabel).join(", ")})`;
  }
}

async function loadReviewConfiguration() {
  try {
    const response = await fetch("/health");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not load review configuration.");
    updateConfiguredReviewProvider(payload.review_config || { provider: payload.clip_review_provider });
  } catch (error) {
    updateConfiguredReviewProvider(null);
  }
}

function projectDisplayName(project) {
  const title = String(project?.title || "").trim() || `Project ${project.id}`;
  const stage = project.current_stage || project.stage || "waiting";
  const clipCount = Number(project.clip_count || 0);
  return `${title} | #${project.id} | ${project.status || "unknown"} | ${stage} | ${clipCount} clips`;
}

function selectedProjectIdFromStorage() {
  const rawValue = window.localStorage.getItem(SELECTED_PROJECT_STORAGE_KEY);
  if (!rawValue) return null;
  const parsed = Number(rawValue);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function shortText(clip) {
  const text = clip.summary || clip.text || "No summary available.";
  return text.length > 112 ? `${text.slice(0, 109)}...` : text;
}

function setRangeBounds(input, min, max, value) {
  input.min = Number(min).toFixed(2);
  input.max = Number(max).toFixed(2);
  input.value = Number(value).toFixed(2);
}

function validationMessage(start = state.editedStart, end = state.editedEnd) {
  const clip = state.selectedClip;
  if (!clip) return "Select a draft clip to start trimming.";
  if (start < clip.min_start) return `Start cannot be before ${seconds(clip.min_start)}.`;
  if (start > end - 10) return "Start must stay at least 10 seconds before end.";
  if (end > clip.max_end) return `End cannot be after ${seconds(clip.max_end)}.`;
  if (end < start + 10) return "End must stay at least 10 seconds after start.";
  if (end - start > 90) return "Duration cannot exceed 90 seconds.";
  return "";
}

function timelinePercent(value) {
  const clip = state.selectedClip;
  if (!clip) return 0;
  const min = clip.min_start;
  const max = clip.max_end;
  if (max <= min) return 0;
  return clamp(((value - min) / (max - min)) * 100, 0, 100);
}

function updateTimelineVisuals() {
  const left = timelinePercent(state.editedStart);
  const right = timelinePercent(state.editedEnd);
  els.timelineRange.style.left = `${left}%`;
  els.timelineRange.style.width = `${Math.max(0, right - left)}%`;
  els.playhead.style.left = `${timelinePercent(els.previewVideo.currentTime || state.editedStart)}%`;
}

function updateReadouts() {
  els.currentTime.textContent = seconds(els.previewVideo.currentTime || state.editedStart);
  els.editedStart.textContent = seconds(state.editedStart);
  els.editedEnd.textContent = seconds(state.editedEnd);
  els.editedDuration.textContent = seconds(duration());
  els.previewTime.value = Number(els.previewVideo.currentTime || state.editedStart).toFixed(2);
  els.startSlider.value = Number(state.editedStart).toFixed(2);
  els.endSlider.value = Number(state.editedEnd).toFixed(2);
  els.startInput.value = Number(state.editedStart).toFixed(2);
  els.endInput.value = Number(state.editedEnd).toFixed(2);

  const warning = validationMessage();
  els.validationMessage.textContent = warning;
  els.validationMessage.hidden = !warning;
  els.renderButton.disabled = Boolean(warning);
  updateTimelineVisuals();
}

function setVideoTime(value) {
  const clip = state.selectedClip;
  if (!clip) return;
  const time = clamp(Number(value), clip.min_start, clip.max_end);
  els.previewVideo.currentTime = time;
  els.previewTime.value = time.toFixed(2);
  updateReadouts();
}

function applyStart(value, { jump = true } = {}) {
  const clip = state.selectedClip;
  if (!clip) return;
  const maxStart = Math.min(clip.max_start, state.editedEnd - 10);
  state.editedStart = Number(clamp(Number(value), clip.min_start, maxStart).toFixed(2));
  if (state.editedEnd - state.editedStart > 90) {
    state.editedEnd = Number((state.editedStart + 90).toFixed(2));
  }
  if (jump) setVideoTime(state.editedStart);
  updateReadouts();
}

function applyEnd(value) {
  const clip = state.selectedClip;
  if (!clip) return;
  const minEnd = Math.max(clip.min_end, state.editedStart + 10);
  const maxEnd = Math.min(clip.max_end, state.editedStart + 90);
  state.editedEnd = Number(clamp(Number(value), minEnd, maxEnd).toFixed(2));
  if (els.previewVideo.currentTime > state.editedEnd || els.previewVideo.currentTime < state.editedStart) {
    setVideoTime(state.editedStart);
  }
  updateReadouts();
}

function configureEditorBounds(clip) {
  const min = clip.min_start;
  const max = clip.max_end;
  [els.previewTime, els.startSlider, els.endSlider].forEach((input) => {
    input.min = Number(min).toFixed(2);
    input.max = Number(max).toFixed(2);
    input.step = "0.01";
  });
  setRangeBounds(els.startInput, clip.min_start, clip.max_start, clip.edited_start ?? clip.ai_start);
  setRangeBounds(els.endInput, clip.min_end, clip.max_end, clip.edited_end ?? clip.ai_end);
}

function renderClipList() {
  els.clipList.textContent = "";
  els.clipCount.textContent = String(state.clips.length);
  els.reviewAllButton.disabled = state.clips.length === 0;

  state.clips.forEach((clip) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "clip-item";
    if (state.selectedClip?.id === clip.id) button.classList.add("active");
    button.innerHTML = `
      <span class="clip-title">Clip ${clip.index}</span>
      <span class="clip-duration">${seconds(clip.duration)}</span>
      <span class="clip-status">${clip.status || "draft"}</span>
      <span class="clip-review-meta"></span>
      <span class="clip-copy"></span>
    `;
    const meta = button.querySelector(".clip-review-meta");
    [
      clip.latest_review_decision ? `Review: ${clip.latest_review_decision}` : "Review: pending",
      clip.latest_review_provider ? `Provider: ${providerLabel(clip.latest_review_provider)}` : "Provider: none",
      `Source: ${clip.boundary_source || "heuristic"}`,
      clip.latest_review_changed_boundaries ? "AI changed bounds" : "Bounds unchanged",
    ].forEach((label, index) => {
      const item = document.createElement("span");
      item.textContent = label;
      if (index === 3 && clip.latest_review_changed_boundaries) item.classList.add("changed");
      meta.appendChild(item);
    });
    button.querySelector(".clip-copy").textContent = shortText(clip);
    button.addEventListener("click", () => selectClip(clip.id));
    els.clipList.appendChild(button);
  });
}

function renderProjectSelector() {
  els.projectSelector.textContent = "";
  if (!state.projects.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No projects";
    els.projectSelector.appendChild(option);
    els.projectSelector.disabled = true;
    return;
  }

  els.projectSelector.disabled = false;
  state.projects.forEach((project) => {
    const option = document.createElement("option");
    option.value = String(project.id);
    option.textContent = projectDisplayName(project);
    els.projectSelector.appendChild(option);
  });
}

function resetVideoSource() {
  state.sourceVideoAvailable = false;
  state.sourceVideoUrl = null;
  state.sourceVideoProjectId = null;
  state.sourceVideoRequestId += 1;
  els.previewVideo.pause();
  els.previewVideo.removeAttribute("src");
  els.previewVideo.load();
}

function clearClipWorkspace(
  message = "Select a ready project to load clips.",
  { projectId = null, sourceStatus = null, showWarning = true } = {},
) {
  state.clips = [];
  state.selectedClip = null;
  state.activeProjectId = projectId ? Number(projectId) : null;
  resetVideoSource();
  els.emptyPreview.hidden = false;
  els.emptyPreview.textContent = message;
  els.sourceStatus.textContent =
    sourceStatus || (state.activeFlowProjectId ? "Project selected" : "No project selected");
  els.sourceStatus.classList.remove("ready");
  els.sourceWarning.hidden = !showWarning;
  els.sourceWarning.textContent = message;
  els.selectedTitle.textContent = "No clip selected";
  els.selectedStatus.textContent = "";
  els.selectedSummary.textContent = "";
  els.selectedTranscript.textContent = "";
  els.selectedScore.textContent = "";
  els.selectedReasons.textContent = "";
  els.aiReviewPanel.hidden = true;
  els.aiReviewDetails.textContent = "";
  els.renderResult.hidden = true;
  els.renderResult.textContent = "";
  els.aiReviewStatus.textContent = "";
  updateHistoricalReviewProvider();
  renderClipList();
  updateReadouts();
}

function markSourceVideoReady() {
  if (!state.sourceVideoUrl || els.previewVideo.getAttribute("src") !== state.sourceVideoUrl) return;
  state.sourceVideoAvailable = true;
  els.sourceStatus.textContent = `Project ${state.sourceVideoProjectId} source ready`;
  els.sourceStatus.classList.add("ready");
  els.sourceWarning.hidden = true;
  els.sourceWarning.textContent = "";
  if (state.selectedClip) {
    els.emptyPreview.hidden = true;
    setVideoTime(state.editedStart);
  }
}

function markSourceVideoFailed() {
  if (!state.sourceVideoUrl || els.previewVideo.getAttribute("src") !== state.sourceVideoUrl) return;
  const message = `Could not load source video for Project ${state.sourceVideoProjectId}.`;
  state.sourceVideoAvailable = false;
  els.sourceStatus.textContent = "Source video error";
  els.sourceStatus.classList.remove("ready");
  els.sourceWarning.hidden = false;
  els.sourceWarning.textContent = message;
  els.emptyPreview.hidden = false;
  els.emptyPreview.textContent = message;
}

function loadProjectSourceVideo(projectId) {
  const sourceUrl = projectSourceVideoUrl(projectId);
  const currentSource = els.previewVideo.getAttribute("src");
  state.sourceVideoProjectId = Number(projectId);
  state.sourceVideoUrl = sourceUrl;

  if (currentSource === sourceUrl && state.sourceVideoAvailable) {
    markSourceVideoReady();
    return;
  }

  state.sourceVideoAvailable = false;
  state.sourceVideoRequestId += 1;
  els.sourceStatus.textContent = `Loading Project ${projectId} source video`;
  els.sourceStatus.classList.remove("ready");
  els.sourceWarning.hidden = true;
  els.sourceWarning.textContent = "";
  els.emptyPreview.hidden = false;
  els.emptyPreview.textContent = `Loading source video for Project ${projectId}...`;

  if (currentSource !== sourceUrl) {
    els.previewVideo.src = sourceUrl;
    els.previewVideo.load();
  } else if (els.previewVideo.readyState >= 1) {
    markSourceVideoReady();
  }
}

function selectClip(clipId) {
  const clip = state.clips.find((item) => item.id === clipId);
  if (!clip) return;
  state.selectedClip = clip;
  state.editedStart = clip.edited_start ?? clip.ai_start;
  state.editedEnd = clip.edited_end ?? clip.ai_end;

  configureEditorBounds(clip);
  els.selectedTitle.textContent = `Clip ${clip.index}`;
  els.selectedStatus.textContent = `Status: ${clip.status || "draft"} | Render: ${clip.render_status || "not_rendered"}`;
  els.selectedSummary.textContent = clip.summary || "No summary available.";
  els.selectedTranscript.textContent = clip.text || "No transcript excerpt available.";
  els.selectedScore.textContent = clip.local_score ? `Local score: ${clip.local_score}` : "Local score unavailable.";
  els.selectedReasons.textContent = "";
  (clip.selection_reasons || []).forEach((reason) => {
    const item = document.createElement("li");
    item.textContent = reason;
    els.selectedReasons.appendChild(item);
  });
  els.scoreDetails.open = false;
  els.transcriptDetails.open = false;
  renderAiReviewPanel(clip);
  els.emptyPreview.hidden = true;
  els.renderResult.hidden = true;
  els.renderResult.textContent = "";

  if (state.activeProjectId) {
    loadProjectSourceVideo(state.activeProjectId);
  }
  setVideoTime(state.editedStart);
  renderClipList();
  updateReadouts();
}

function mergeClip(updatedClip) {
  const index = state.clips.findIndex((item) => item.id === updatedClip.id);
  if (index >= 0) {
    state.clips[index] = { ...state.clips[index], ...updatedClip };
    state.selectedClip = state.clips[index];
  }
  renderClipList();
  updateHistoricalReviewProvider();
  if (state.selectedClip) {
    els.selectedStatus.textContent = `Status: ${state.selectedClip.status || "draft"} | Render: ${state.selectedClip.render_status || "not_rendered"}`;
    renderAiReviewPanel(state.selectedClip);
  }
}

function renderAiReviewPanel(clip) {
  els.aiReviewDetails.textContent = "";
  const hasReview = Boolean(clip.latest_review_decision || clip.reviewed_start || clip.latest_review_reasoning_summary);
  els.aiReviewPanel.hidden = !hasReview;
  if (!hasReview) return;

  const rows = [
    ["Decision", clip.latest_review_decision || "pending"],
    ["Boundary source", clip.boundary_source || "heuristic"],
    ["Original AI", `${seconds(clip.ai_start)} to ${seconds(clip.ai_end)}`],
    ["Reviewed bounds", formatRange(clip.reviewed_start, clip.reviewed_end)],
    ["Current edited", `${seconds(clip.edited_start)} to ${seconds(clip.edited_end)}`],
    ["Changed", clip.latest_review_changed_boundaries ? "Yes" : "No"],
    ["Reasoning", clip.latest_review_reasoning_summary || ""],
    ["Start reason", clip.latest_review_start_reason || ""],
    ["End reason", clip.latest_review_end_reason || ""],
    ["Warnings", (clip.latest_review_warnings || []).join(" ")],
  ];
  rows.forEach(([label, value]) => appendReviewRow(label, value));
}

function formatRange(start, end) {
  if (!hasValue(start) || !hasValue(end)) return "Not set";
  return `${seconds(start)} to ${seconds(end)}`;
}

function appendReviewRow(label, value) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value || "None";
  els.aiReviewDetails.appendChild(dt);
  els.aiReviewDetails.appendChild(dd);
}

async function saveBounds() {
  if (!state.selectedClip) return null;
  const url = state.activeProjectId
    ? `/projects/${encodeURIComponent(state.activeProjectId)}/clips/${encodeURIComponent(state.selectedClip.id)}`
    : `/clips/${encodeURIComponent(state.selectedClip.id)}`;
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      start: state.editedStart,
      end: state.editedEnd,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail || "Could not save clip bounds.";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  mergeClip(payload.clip);
  return payload.clip;
}

async function setSelectedStatus(action) {
  if (!state.selectedClip) return;
  const warning = validationMessage();
  if (warning && action === "accept") {
    els.validationMessage.textContent = warning;
    els.validationMessage.hidden = false;
    return;
  }
  try {
    await saveBounds();
    const url = state.activeProjectId
      ? `/projects/${encodeURIComponent(state.activeProjectId)}/clips/${encodeURIComponent(state.selectedClip.id)}/${action}`
      : `/clips/${encodeURIComponent(state.selectedClip.id)}/${action}`;
    const response = await fetch(url, {
      method: "POST",
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail || `Could not mark clip as ${action}.`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    mergeClip(payload.clip);
  } catch (error) {
    els.validationMessage.textContent = error.message;
    els.validationMessage.hidden = false;
  }
}

function ensurePreviewStartsInRange() {
  if (!state.selectedClip) return;
  if (els.previewVideo.currentTime < state.editedStart || els.previewVideo.currentTime >= state.editedEnd) {
    setVideoTime(state.editedStart);
  }
}

async function togglePlay() {
  if (!state.selectedClip || !state.sourceVideoAvailable) return;
  if (els.previewVideo.paused) {
    ensurePreviewStartsInRange();
    await els.previewVideo.play();
  } else {
    els.previewVideo.pause();
  }
}

async function renderShort() {
  if (!state.selectedClip) return;
  const warning = validationMessage();
  if (warning) return;

  els.renderButton.disabled = true;
  els.renderButton.textContent = "Rendering...";
  els.renderResult.hidden = true;
  els.validationMessage.hidden = true;
  els.validationMessage.textContent = "";

  try {
    await saveBounds();
    const response = await fetch(state.activeProjectId ? `${projectBase()}/render` : "/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clip_id: state.selectedClip.id,
        start: state.editedStart,
        end: state.editedEnd,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail?.message || payload.detail || "Render failed.";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    els.renderResult.textContent = JSON.stringify(
      {
        status: payload.status,
        output_dir: payload.output_dir,
        raw_outputs: payload.raw_outputs,
        subtitled_outputs: payload.subtitled_outputs,
        warnings: payload.warnings,
      },
      null,
      2,
    );
    els.renderResult.hidden = false;
    if (payload.clip) {
      mergeClip(payload.clip);
    } else {
      await loadClips({ projectId: state.activeProjectId });
    }
  } catch (error) {
    els.validationMessage.textContent = error.message;
    els.validationMessage.hidden = false;
  } finally {
    els.renderButton.textContent = "Render Short";
    els.renderButton.disabled = Boolean(validationMessage());
  }
}

async function reviewAllWithAi() {
  const projectId = state.activeProjectId || state.activeFlowProjectId || state.selectedClip?.project_id || state.clips[0]?.project_id;
  if (!projectId) return;
  const previousClipId = state.selectedClip?.id;
  els.reviewAllButton.disabled = true;
  els.aiReviewStatus.textContent = `Reviewing ${state.clips.length} clips with AI...`;
  updateHistoricalReviewProvider("updating");
  els.validationMessage.hidden = true;
  els.validationMessage.textContent = "";

  try {
    const response = await fetch(`/projects/${encodeURIComponent(projectId)}/review-clips`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apply_safe_suggestions: true }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail || "AI review failed.";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    els.aiReviewStatus.textContent =
      `${payload.provider || "AI"} reviewed ${payload.clip_count} clips: ` +
      `${payload.render_ready_count} ready, ${payload.adjust_boundaries_count} adjusted, ` +
      `${payload.reject_count} rejected, ${payload.manual_review_count} manual, ${payload.failed_count} failed.`;
    updateHistoricalReviewProvider(payload.provider || state.configuredReviewProvider);
    await loadClips({ selectClipId: previousClipId, projectId, preserveVideo: true });
  } catch (error) {
    els.aiReviewStatus.textContent = error.message;
    updateHistoricalReviewProvider();
  } finally {
    els.reviewAllButton.disabled = state.clips.length === 0;
  }
}

function wireEvents() {
  els.previewVideo.addEventListener("timeupdate", () => {
    if (state.selectedClip && els.previewVideo.currentTime >= state.editedEnd) {
      if (state.loopPreview) {
        els.previewVideo.currentTime = state.editedStart;
        if (els.previewVideo.paused) {
          els.previewVideo.play().catch(() => {});
        }
      } else {
        els.previewVideo.pause();
        els.previewVideo.currentTime = state.editedEnd;
      }
    }
    updateReadouts();
  });

  els.previewVideo.addEventListener("play", () => {
    ensurePreviewStartsInRange();
    els.playPause.textContent = "Pause";
  });

  els.previewVideo.addEventListener("pause", () => {
    els.playPause.textContent = "Play";
  });

  els.previewVideo.addEventListener("loadedmetadata", markSourceVideoReady);
  els.previewVideo.addEventListener("error", markSourceVideoFailed);

  els.previewTime.addEventListener("input", () => setVideoTime(els.previewTime.value));
  els.startSlider.addEventListener("input", () => applyStart(els.startSlider.value));
  els.endSlider.addEventListener("input", () => applyEnd(els.endSlider.value));
  els.startInput.addEventListener("input", () => applyStart(els.startInput.value));
  els.endInput.addEventListener("input", () => applyEnd(els.endInput.value));

  els.playPause.addEventListener("click", togglePlay);
  els.jumpStart.addEventListener("click", () => setVideoTime(state.editedStart));
  els.loopToggle.addEventListener("click", () => {
    state.loopPreview = !state.loopPreview;
    els.loopToggle.textContent = `Loop Preview: ${state.loopPreview ? "ON" : "OFF"}`;
    els.loopToggle.setAttribute("aria-pressed", String(state.loopPreview));
  });
  els.acceptButton.addEventListener("click", () => setSelectedStatus("accept"));
  els.rejectButton.addEventListener("click", () => setSelectedStatus("reject"));
  els.renderButton.addEventListener("click", renderShort);
  els.reviewAllButton.addEventListener("click", reviewAllWithAi);
  els.projectSelector.addEventListener("change", () => {
    const projectId = Number(els.projectSelector.value);
    if (projectId) selectProject(projectId, { persist: true });
  });
  els.refreshProjectsButton.addEventListener("click", () => loadProjects({ preferredProjectId: state.activeFlowProjectId }));
  els.createProjectButton.addEventListener("click", createProject);
  els.startProjectButton.addEventListener("click", startProject);
  els.cancelProjectButton.addEventListener("click", cancelProject);
  els.openProjectClipsButton.addEventListener("click", () => loadClips({ projectId: state.activeFlowProjectId }));
}

async function initializeProjectFlow() {
  clearClipWorkspace("Loading projects...");
  await loadReviewConfiguration();
  await loadProjects();
}

async function loadProjects({ preferredProjectId = null } = {}) {
  try {
    const response = await fetch("/projects");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not load projects.");
    state.projects = [...(payload.projects || [])].sort((left, right) => {
      const leftTime = Date.parse(left.updated_at || left.created_at || "") || 0;
      const rightTime = Date.parse(right.updated_at || right.created_at || "") || 0;
      return rightTime - leftTime || Number(right.id || 0) - Number(left.id || 0);
    });
    renderProjectSelector();

    if (!state.projects.length) {
      state.activeFlowProjectId = null;
      window.localStorage.removeItem(SELECTED_PROJECT_STORAGE_KEY);
      setProjectFlowStatus("No projects found.", 0);
      clearClipWorkspace("Create a project to start reviewing clips.");
      return;
    }

    const storedProjectId = preferredProjectId || selectedProjectIdFromStorage();
    const restoredProject = state.projects.find((project) => Number(project.id) === Number(storedProjectId));
    const selectedProject = restoredProject || state.projects[0];
    await selectProject(selectedProject.id, { persist: true });
  } catch (error) {
    setProjectFlowStatus(error.message, 0);
    clearClipWorkspace(error.message);
  }
}

async function selectProject(projectId, { persist = true } = {}) {
  const selectedId = Number(projectId);
  const project = state.projects.find((item) => Number(item.id) === selectedId);
  if (!project) {
    state.activeFlowProjectId = null;
    state.projectSelectionRequestId += 1;
    state.clipLoadRequestId += 1;
    if (persist) window.localStorage.removeItem(SELECTED_PROJECT_STORAGE_KEY);
    setProjectFlowStatus("Selected project no longer exists.", 0);
    clearClipWorkspace("Select a project to load clips.");
    return;
  }

  state.activeFlowProjectId = selectedId;
  const selectionRequestId = ++state.projectSelectionRequestId;
  els.projectSelector.value = String(selectedId);
  if (persist) window.localStorage.setItem(SELECTED_PROJECT_STORAGE_KEY, String(selectedId));

  state.clipLoadRequestId += 1;
  clearClipWorkspace(`Loading Project ${selectedId}...`, {
    projectId: selectedId,
    sourceStatus: `Loading Project ${selectedId}`,
    showWarning: false,
  });

  try {
    const status = await fetchProjectStatus(selectedId);
    if (selectionRequestId !== state.projectSelectionRequestId || selectedId !== Number(state.activeFlowProjectId)) return;
    updateProjectStatus(status);
    await loadProjectLogs(selectedId);
    if (selectionRequestId !== state.projectSelectionRequestId || selectedId !== Number(state.activeFlowProjectId)) return;
    if (status.status === "ready") {
      await loadClips({ projectId: selectedId });
    } else {
      clearClipWorkspace(`Project ${selectedId} is ${status.status || "not ready"}.`, {
        projectId: selectedId,
        sourceStatus: `Project ${selectedId} selected`,
      });
    }
  } catch (error) {
    setProjectFlowStatus(error.message, 0);
    clearClipWorkspace(error.message, {
      projectId: selectedId,
      sourceStatus: "Error",
    });
  }
}

async function fetchProjectStatus(projectId) {
  const response = await fetch(`/projects/${encodeURIComponent(projectId)}/status`);
  const status = await response.json();
  if (!response.ok) throw new Error(status.detail || "Could not load project status.");
  return status;
}

async function createProject() {
  const sourceUrl = els.projectUrlInput.value.trim();
  if (!sourceUrl) {
    setProjectFlowStatus("Enter a YouTube URL first.", 0);
    return;
  }
  els.createProjectButton.disabled = true;
  setProjectFlowStatus("Creating project...", 0);
  try {
    const response = await fetch("/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_url: sourceUrl,
        title: els.projectTitleInput.value.trim() || null,
        auto_review: els.projectAutoReview.checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not create project.");
    await loadProjects({ preferredProjectId: payload.project.id });
  } catch (error) {
    setProjectFlowStatus(error.message, 0);
  } finally {
    els.createProjectButton.disabled = false;
  }
}

async function startProject() {
  if (!state.activeFlowProjectId) return;
  els.startProjectButton.disabled = true;
  setProjectFlowStatus("Starting project pipeline...", 0);
  try {
    const response = await fetch(`/projects/${encodeURIComponent(state.activeFlowProjectId)}/start`, {
      method: "POST",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not start project.");
    const status = await fetchProjectStatus(state.activeFlowProjectId);
    updateProjectStatus(status);
    startProjectPolling();
  } catch (error) {
    setProjectFlowStatus(error.message, Number(els.projectProgress.value || 0));
    els.startProjectButton.disabled = false;
  }
}

async function cancelProject() {
  if (!state.activeFlowProjectId) return;
  els.cancelProjectButton.disabled = true;
  try {
    const response = await fetch(`/projects/${encodeURIComponent(state.activeFlowProjectId)}/cancel`, {
      method: "POST",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not cancel project.");
    const status = await fetchProjectStatus(state.activeFlowProjectId);
    updateProjectStatus(status);
    await loadProjects({ preferredProjectId: state.activeFlowProjectId });
  } catch (error) {
    setProjectFlowStatus(error.message, Number(els.projectProgress.value || 0));
  }
}

function startProjectPolling() {
  if (state.projectPollingTimer) clearInterval(state.projectPollingTimer);
  state.projectPollingTimer = setInterval(pollProjectStatus, 2000);
  pollProjectStatus();
}

async function pollProjectStatus() {
  if (!state.activeFlowProjectId) return;
  try {
    const status = await fetchProjectStatus(state.activeFlowProjectId);
    updateProjectStatus(status);
    await loadProjectLogs();
    if (["ready", "failed", "cancelled"].includes(status.status)) {
      clearInterval(state.projectPollingTimer);
      state.projectPollingTimer = null;
      await loadProjects({ preferredProjectId: state.activeFlowProjectId });
      if (status.status === "ready") {
        await loadClips({ projectId: state.activeFlowProjectId });
      }
    }
  } catch (error) {
    setProjectFlowStatus(error.message, Number(els.projectProgress.value || 0));
  }
}

async function loadProjectLogs(projectId = state.activeFlowProjectId) {
  if (!projectId) return;
  const requestedProjectId = Number(projectId);
  const response = await fetch(`/projects/${encodeURIComponent(requestedProjectId)}/logs?tail=80`);
  if (!response.ok) return;
  const payload = await response.json();
  if (requestedProjectId !== Number(state.activeFlowProjectId)) return;
  els.projectLogTail.textContent = (payload.lines || []).join("\n");
}

function updateProjectStatus(status) {
  if (!status) return;
  const progress = Number(status.progress_percent || 0);
  const stage = status.stage || status.current_stage || "waiting";
  const message = status.error_message || status.message || stage;
  setProjectFlowStatus(`Project ${status.project_id}: ${status.status} / ${stage} / ${progress.toFixed(0)}% - ${message}`, progress);
  els.openProjectClipsButton.disabled = status.status !== "ready";
  els.startProjectButton.disabled = ["queued", "running", "ready"].includes(status.status);
  els.cancelProjectButton.disabled = !["queued", "running"].includes(status.status);
  const project = state.projects.find((item) => Number(item.id) === Number(status.project_id));
  if (project) {
    project.status = status.status;
    project.current_stage = stage;
    project.stage = stage;
    project.progress_percent = progress;
    project.clip_count = status.clip_count ?? project.clip_count;
    renderProjectSelector();
    els.projectSelector.value = String(status.project_id);
  }
}

function setProjectFlowStatus(message, progress) {
  els.projectFlowStatus.textContent = message;
  els.projectProgress.value = Math.max(0, Math.min(100, Number(progress || 0)));
}

async function loadClips({ selectClipId = null, projectId = null, preserveVideo = false } = {}) {
  if (!projectId) {
    clearClipWorkspace("Select a ready project to load clips.");
    return;
  }
  const requestedProjectId = Number(projectId);
  const requestId = ++state.clipLoadRequestId;
  if (!preserveVideo) {
    clearClipWorkspace(`Loading clips for Project ${requestedProjectId}...`, {
      projectId: requestedProjectId,
      sourceStatus: `Loading Project ${requestedProjectId} clips`,
      showWarning: false,
    });
  }
  try {
    const previousClipId = selectClipId || state.selectedClip?.id;
    state.activeProjectId = requestedProjectId;
    const response = await fetch(`/projects/${encodeURIComponent(requestedProjectId)}/clips`);
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, "Could not load project clips."));
    }
    const payload = await response.json();
    if (requestId !== state.clipLoadRequestId || requestedProjectId !== Number(state.activeFlowProjectId)) return;
    state.clips = Array.isArray(payload) ? payload : payload.clips || [];

    els.sourceStatus.textContent = `Project ${requestedProjectId} clips loaded`;
    els.sourceStatus.classList.remove("ready");
    els.sourceWarning.hidden = true;
    els.sourceWarning.textContent = "";

    renderClipList();
    updateHistoricalReviewProvider();
    if (state.clips.length) {
      const selected = state.clips.find((clip) => clip.id === previousClipId) || state.clips[0];
      selectClip(selected.id);
    } else {
      els.emptyPreview.textContent = "No draft clips found";
      els.emptyPreview.hidden = false;
      resetVideoSource();
    }
  } catch (error) {
    if (requestId !== state.clipLoadRequestId) return;
    clearClipWorkspace(error.message, {
      projectId: requestedProjectId,
      sourceStatus: "Error",
    });
    els.sourceStatus.textContent = "Error";
    els.sourceWarning.hidden = false;
    els.sourceWarning.textContent = error.message;
  }
}

wireEvents();
initializeProjectFlow();
