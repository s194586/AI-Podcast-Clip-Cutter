const els = {
  clipList: document.querySelector("#clipList"),
  clipCount: document.querySelector("#clipCount"),
  sourceStatus: document.querySelector("#sourceStatus"),
  sourceWarning: document.querySelector("#sourceWarning"),
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
  renderResult: document.querySelector("#renderResult"),
};

const state = {
  clips: [],
  selectedClip: null,
  editedStart: 0,
  editedEnd: 0,
  loopPreview: true,
  sourceVideoAvailable: false,
};

const seconds = (value) => `${Number(value || 0).toFixed(2)}s`;
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const duration = () => state.editedEnd - state.editedStart;

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

  state.clips.forEach((clip) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "clip-item";
    if (state.selectedClip?.id === clip.id) button.classList.add("active");
    button.innerHTML = `
      <span class="clip-title">Clip ${clip.index}</span>
      <span class="clip-duration">${seconds(clip.duration)}</span>
      <span class="clip-status">${clip.status || "draft"}</span>
      <span class="clip-copy"></span>
    `;
    button.querySelector(".clip-copy").textContent = shortText(clip);
    button.addEventListener("click", () => selectClip(clip.id));
    els.clipList.appendChild(button);
  });
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
  els.emptyPreview.hidden = true;
  els.renderResult.hidden = true;
  els.renderResult.textContent = "";

  if (state.sourceVideoAvailable && !els.previewVideo.src) {
    els.previewVideo.src = "/source-video";
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
  if (state.selectedClip) {
    els.selectedStatus.textContent = `Status: ${state.selectedClip.status || "draft"} | Render: ${state.selectedClip.render_status || "not_rendered"}`;
  }
}

async function saveBounds() {
  if (!state.selectedClip) return null;
  const response = await fetch(`/clips/${encodeURIComponent(state.selectedClip.id)}`, {
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
    const response = await fetch(`/clips/${encodeURIComponent(state.selectedClip.id)}/${action}`, {
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
    const response = await fetch("/render", {
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
      await loadClips();
    }
  } catch (error) {
    els.validationMessage.textContent = error.message;
    els.validationMessage.hidden = false;
  } finally {
    els.renderButton.textContent = "Render Short";
    els.renderButton.disabled = Boolean(validationMessage());
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
}

async function loadClips() {
  try {
    const response = await fetch("/clips");
    if (!response.ok) throw new Error(await response.text());
    const payload = await response.json();
    state.clips = Array.isArray(payload) ? payload : payload.clips || [];
    state.sourceVideoAvailable = Boolean(payload.source_video_available);

    els.sourceStatus.textContent = state.sourceVideoAvailable ? "Source video ready" : "Demo clips only";
    els.sourceStatus.classList.toggle("ready", state.sourceVideoAvailable);
    els.sourceWarning.hidden = state.sourceVideoAvailable;
    els.sourceWarning.textContent = state.sourceVideoAvailable
      ? ""
      : "Missing source video. Put or download an mp4, mov, mkv, or webm file into input/ to preview and render real clips.";

    if (state.sourceVideoAvailable) {
      els.previewVideo.src = payload.source_video_url || "/source-video";
    }

    renderClipList();
    if (state.clips.length) {
      selectClip(state.clips[0].id);
    } else {
      els.emptyPreview.textContent = "No draft clips found";
    }
  } catch (error) {
    els.sourceStatus.textContent = "Error";
    els.sourceWarning.hidden = false;
    els.sourceWarning.textContent = error.message;
  }
}

wireEvents();
loadClips();
