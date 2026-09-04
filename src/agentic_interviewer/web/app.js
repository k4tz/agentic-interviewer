(() => {
  "use strict";

  const SILENCE_NUDGE_MS = 12000;
  const ANSWER_LIMIT_MS = 90000;

  const $ = (id) => document.getElementById(id);
  const elements = {
    systemDot: $("systemDot"),
    systemLabel: $("systemLabel"),
    systemDetail: $("systemDetail"),
    intakeForm: $("intakeForm"),
    jobSelect: $("jobSelect"),
    jobBrief: $("jobBrief"),
    resumeInput: $("resumeInput"),
    uploadZone: $("uploadZone"),
    uploadTitleText: $("uploadTitleText"),
    uploadHint: $("uploadHint"),
    prepareButton: $("prepareButton"),
    intakeStatus: $("intakeStatus"),
    preparedCount: $("preparedCount"),
    micDevice: $("micDevice"),
    micDetail: $("micDetail"),
    levelBar: $("levelBar"),
    checkMicButton: $("checkMicButton"),
    cameraDevice: $("cameraDevice"),
    cameraDetail: $("cameraDetail"),
    cameraPreview: $("cameraPreview"),
    cameraVideo: $("cameraVideo"),
    cameraButton: $("cameraButton"),
    planningWarning: $("planningWarning"),
    planningWarningText: $("planningWarningText"),
    planningAcknowledgement: $("planningAcknowledgement"),
    backButton: $("backButton"),
    beginButton: $("beginButton"),
    interviewStage: $("interviewStage"),
    sessionRole: $("sessionRole"),
    questionCounter: $("questionCounter"),
    elapsedTime: $("elapsedTime"),
    questionText: $("questionText"),
    replayButton: $("replayButton"),
    audioThread: $("audioThread"),
    threadLabel: $("threadLabel"),
    threadDetail: $("threadDetail"),
    liveTranscript: $("liveTranscript"),
    answerTimer: $("answerTimer"),
    recordButton: $("recordButton"),
    recordLabel: $("recordLabel"),
    typeFallbackButton: $("typeFallbackButton"),
    typedAnswerForm: $("typedAnswerForm"),
    typedAnswer: $("typedAnswer"),
    cancelTypedButton: $("cancelTypedButton"),
    conversationLog: $("conversationLog"),
    completionStage: $("completionStage"),
    completedRole: $("completedRole"),
    completedAnswers: $("completedAnswers"),
    toast: $("toast")
  };

  const state = {
    stage: "upload",
    jobs: [],
    selectedFile: null,
    intake: null,
    intakePending: false,
    intakeRequestId: 0,
    planningAcknowledged: false,
    micReady: false,
    micPreviewStream: null,
    micPreviewContext: null,
    micPreviewFrame: 0,
    cameraStream: null,
    socket: null,
    socketReady: false,
    recording: false,
    speechDetected: false,
    awaitingTranscript: false,
    transcriptSubmitted: false,
    mediaStream: null,
    audioContext: null,
    worklet: null,
    source: null,
    silentGain: null,
    flushResolve: null,
    transcriptTimeout: 0,
    answerStartedAt: 0,
    answerClock: 0,
    silenceNudgeTimeout: 0,
    answerDeadlineTimeout: 0,
    questionDeadlineAt: 0,
    silenceNudged: false,
    sessionStartedAt: 0,
    elapsedClock: 0,
    currentQuestion: "",
    currentQuestionIndex: 0,
    questionTotal: 0,
    answers: 0,
    speechAudio: null,
    speechUrl: null
  };

  function setSystem(label, detail, mode = "pending") {
    elements.systemLabel.textContent = label;
    elements.systemDetail.textContent = detail;
    elements.systemDot.dataset.state = mode;
  }

  function setThread(mode, label, detail) {
    elements.audioThread.dataset.mode = mode;
    elements.threadLabel.textContent = label;
    elements.threadDetail.textContent = detail;
  }

  function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    window.clearTimeout(showToast.timeout);
    showToast.timeout = window.setTimeout(() => { elements.toast.hidden = true; }, 6500);
  }

  function setStage(name) {
    state.stage = name;
    document.querySelectorAll("[data-stage]").forEach((panel) => {
      const active = panel.dataset.stage === name;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    const order = ["upload", "device", "interview"];
    const activeIndex = order.indexOf(name === "complete" ? "interview" : name);
    document.querySelectorAll(".phase").forEach((phase, index) => {
      phase.classList.toggle("is-current", index === activeIndex && name !== "complete");
      phase.classList.toggle("is-complete", index < activeIndex || name === "complete");
      if (index === activeIndex && name !== "complete") phase.setAttribute("aria-current", "step");
      else phase.removeAttribute("aria-current");
    });
    document.querySelector(`[data-stage="${name}"]`)?.focus?.({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    if (response.ok) return response;
    let message = `Request failed with HTTP ${response.status}`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string"
        ? body.detail
        : body.detail?.message || body.error?.message || message;
    } catch (_) {
      // Keep the bounded status message when an upstream response is not JSON.
    }
    throw new Error(message);
  }

  async function loadJobs() {
    try {
      const response = await api("/api/candidate/jobs");
      const body = await response.json();
      state.jobs = body.jobs || [];
      if (!state.jobs.length) throw new Error("No interview roles are currently available");
      elements.jobSelect.replaceChildren(...state.jobs.map((job) => {
        const option = document.createElement("option");
        option.value = job.id;
        option.textContent = `${job.title} · ${job.company}`;
        return option;
      }));
      elements.jobSelect.disabled = false;
      renderJob();
      updatePrepareAvailability();
      setSystem("Application ready", "Resume intake is available", "ready");
      checkProviderHealth();
    } catch (error) {
      elements.jobSelect.innerHTML = '<option value="">Roles unavailable</option>';
      elements.jobBrief.innerHTML = `<div class="job-brief-skeleton">${escapeHtml(error.message)}</div>`;
      setSystem("Setup unavailable", "Check PostgreSQL and the API", "error");
      showToast(error.message);
    }
  }

  async function checkProviderHealth() {
    try {
      const response = await api("/providers/health");
      const body = await response.json();
      if (body.status === "ok") setSystem("Services ready", "Reasoning and speech providers connected", "ready");
      else setSystem("Fallback available", "One or more model providers are degraded");
    } catch (_) {
      setSystem("Application ready", "Provider health could not be checked");
    }
  }

  function renderJob() {
    const job = selectedJob();
    if (!job) return;
    const requirements = job.requirements.slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    elements.jobBrief.innerHTML = `
      <div class="job-brief-head"><h2>${escapeHtml(job.title)}</h2><span>${escapeHtml(job.location)}</span></div>
      <p>${escapeHtml(job.description)}</p>
      <ul>${requirements}</ul>`;
  }

  function selectedJob() {
    return state.jobs.find((job) => job.id === elements.jobSelect.value) || state.jobs[0];
  }

  function selectResume(file) {
    state.selectedFile = file || null;
    if (file) {
      elements.uploadZone.classList.add("has-file");
      elements.uploadTitleText.textContent = file.name;
      elements.uploadHint.textContent = `${formatBytes(file.size)} · ready to process`;
      elements.intakeStatus.textContent = "Resume selected. Continue when you’re ready.";
      elements.intakeStatus.dataset.tone = "success";
    } else {
      elements.uploadZone.classList.remove("has-file");
      elements.uploadTitleText.textContent = "Drop your resume here";
      elements.uploadHint.textContent = "PDF, TXT, or Markdown · up to 5 MB";
      elements.intakeStatus.textContent = "Your interview is not created until you continue.";
      delete elements.intakeStatus.dataset.tone;
    }
    updatePrepareAvailability();
  }

  function updatePrepareAvailability() {
    elements.prepareButton.disabled = !state.selectedFile || !elements.jobSelect.value;
  }

  function updateBeginAvailability() {
    elements.beginButton.disabled = !state.micReady || state.intakePending || !state.intake;
  }

  async function prepareInterview(event) {
    event.preventDefault();
    if (!state.selectedFile || !elements.jobSelect.value) return;
    const requestId = ++state.intakeRequestId;
    state.intake = null;
    state.intakePending = true;
    state.planningAcknowledged = true;
    elements.prepareButton.disabled = true;
    elements.intakeStatus.textContent = "Resume received. Continue with your device check.";
    delete elements.intakeStatus.dataset.tone;
    elements.preparedCount.textContent = "Check your setup";
    elements.planningWarning.hidden = true;
    updateBeginAvailability();
    setSystem("Device check", "Test your microphone before the interview");
    setStage("device");
    inspectPermissions();
    const form = new FormData();
    form.append("job_id", elements.jobSelect.value);
    form.append("resume", state.selectedFile, state.selectedFile.name);
    try {
      const response = await api("/api/candidate/intakes", { method: "POST", body: form });
      if (requestId !== state.intakeRequestId) return;
      state.intake = await response.json();
      state.intakePending = false;
      state.questionTotal = state.intake.question_count;
      elements.preparedCount.textContent = "Ready when you are";
      elements.intakeStatus.textContent = "Interview setup complete.";
      elements.intakeStatus.dataset.tone = "success";
      elements.planningAcknowledgement.checked = true;
      elements.planningWarning.hidden = true;
      setSystem("Ready to begin", "Complete the microphone check", "ready");
      updateBeginAvailability();
    } catch (error) {
      if (requestId !== state.intakeRequestId) return;
      state.intakePending = false;
      elements.intakeStatus.textContent = error.message;
      elements.intakeStatus.dataset.tone = "error";
      elements.preparedCount.textContent = "Setup needs attention";
      elements.planningWarning.hidden = false;
      elements.planningWarningText.textContent = "Return to the resume step and try again.";
      elements.prepareButton.disabled = false;
      updateBeginAvailability();
      setSystem("Setup unavailable", "Return to the previous step and try again", "error");
      showToast("We couldn’t complete the interview setup. Return to the previous step and try again.");
    }
  }

  async function inspectPermissions() {
    if (!navigator.permissions?.query) return;
    try {
      const microphone = await navigator.permissions.query({ name: "microphone" });
      if (microphone.state === "denied") {
        elements.micDevice.dataset.state = "error";
        elements.micDetail.textContent = "Blocked in browser settings.";
      }
    } catch (_) {
      // Permission introspection is not consistently implemented; getUserMedia is authoritative.
    }
    try {
      const camera = await navigator.permissions.query({ name: "camera" });
      if (camera.state === "denied") elements.cameraDetail.textContent = "Blocked, but not required.";
      if (camera.state === "granted") elements.cameraDetail.textContent = "Previously allowed. Preview remains optional.";
    } catch (_) {
      // The camera remains untouched until the candidate explicitly enables it.
    }
  }

  async function checkMicrophone() {
    elements.checkMicButton.disabled = true;
    elements.micDetail.textContent = "Requesting browser permission…";
    await stopMicPreview();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false
      });
      state.micPreviewStream = stream;
      const context = new AudioContext({ latencyHint: "interactive" });
      await context.resume();
      state.micPreviewContext = context;
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      state.micReady = true;
      elements.micDevice.dataset.state = "ready";
      elements.micDetail.textContent = "Permission granted. Speak to test the level.";
      elements.checkMicButton.textContent = "Check again";
      updateBeginAvailability();
      drawLevel(analyser);
    } catch (error) {
      state.micReady = false;
      elements.micDevice.dataset.state = "error";
      elements.micDetail.textContent = `Unavailable: ${error.message}`;
      elements.beginButton.disabled = true;
      showToast("Microphone access is required to begin the voice interview.");
    } finally {
      elements.checkMicButton.disabled = false;
    }
  }

  function drawLevel(analyser) {
    const data = new Float32Array(analyser.fftSize);
    const frame = () => {
      if (!state.micPreviewStream) return;
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (const value of data) sum += value * value;
      const rms = Math.sqrt(sum / data.length);
      elements.levelBar.style.width = `${Math.min(100, Math.max(3, rms * 450))}%`;
      state.micPreviewFrame = requestAnimationFrame(frame);
    };
    frame();
  }

  async function stopMicPreview() {
    cancelAnimationFrame(state.micPreviewFrame);
    state.micPreviewFrame = 0;
    state.micPreviewStream?.getTracks().forEach((track) => track.stop());
    state.micPreviewStream = null;
    if (state.micPreviewContext && state.micPreviewContext.state !== "closed") {
      await state.micPreviewContext.close();
    }
    state.micPreviewContext = null;
    elements.levelBar.style.width = "3%";
  }

  async function toggleCamera() {
    if (state.cameraStream) {
      stopCamera();
      return;
    }
    elements.cameraButton.disabled = true;
    elements.cameraDetail.textContent = "Requesting optional camera permission…";
    try {
      state.cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      elements.cameraVideo.srcObject = state.cameraStream;
      await elements.cameraVideo.play();
      elements.cameraPreview.hidden = false;
      elements.cameraDevice.dataset.state = "ready";
      elements.cameraDetail.textContent = "Local preview enabled. Video is not uploaded.";
      elements.cameraButton.textContent = "Disable preview";
    } catch (error) {
      elements.cameraDevice.dataset.state = "optional";
      elements.cameraDetail.textContent = "Not enabled. You can continue without it.";
      showToast(`Camera preview was not enabled: ${error.message}`);
    } finally {
      elements.cameraButton.disabled = false;
    }
  }

  function stopCamera() {
    state.cameraStream?.getTracks().forEach((track) => track.stop());
    state.cameraStream = null;
    elements.cameraVideo.srcObject = null;
    elements.cameraPreview.hidden = true;
    elements.cameraDevice.dataset.state = "optional";
    elements.cameraDetail.textContent = "Preview disabled. Camera is not required.";
    elements.cameraButton.textContent = "Enable preview";
  }

  async function beginInterview() {
    if (!state.intake || state.intakePending || !state.micReady) return;
    elements.beginButton.disabled = true;
    await stopMicPreview();
    stopCamera();
    const job = state.intake.job;
    elements.sessionRole.textContent = `${job.title} · ${job.company}`;
    elements.questionCounter.textContent = `Question 1 of ${state.questionTotal}`;
    setStage("interview");
    setSystem("Interview live", "Server-mediated voice session", "ready");
    setThread("connecting", "Connecting to speech service", "Establishing the secure browser proxy");
    let realtimeError = null;
    try {
      await connectRealtime();
    } catch (error) {
      realtimeError = error;
      showToast(`${error.message} You can still type your answers.`);
    }
    try {
      const response = await api(`/interviews/${state.intake.interview_id}/start`, { method: "POST" });
      const interview = await response.json();
      state.sessionStartedAt = performance.now();
      state.currentQuestionIndex = interview.current_question_index || 0;
      state.currentQuestion = interview.last_response;
      elements.questionText.textContent = state.currentQuestion;
      updateQuestionCounter();
      appendConversation("Interviewer", state.currentQuestion);
      startSessionClock();
      await playCurrentQuestion();
      if (realtimeError) setThread("ready", "Typed answers available", "Voice is unavailable; you can continue typing");
    } catch (error) {
      setThread("error", "Interview could not start", error.message);
      setSystem("Interview error", "Refresh after checking the API", "error");
      showToast(error.message);
    }
  }

  function connectRealtime() {
    return new Promise((resolve, reject) => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${scheme}://${location.host}/ws/voice/realtime?language=en`);
      state.socket = socket;
      let settled = false;
      const timeout = window.setTimeout(() => {
        if (!settled) {
          settled = true;
          socket.close();
          reject(new Error("Speech connection timed out."));
        }
      }, 12000);

      socket.addEventListener("message", ({ data }) => {
        let event;
        try { event = JSON.parse(data); } catch (_) { return; }
        if (event.type === "voice.connected") {
          setThread("connecting", "Configuring turn detection", "Preparing automatic answer capture");
          return;
        }
        if (event.type === "voice.ready") {
          state.socketReady = true;
          if (!settled) {
            settled = true;
            window.clearTimeout(timeout);
            resolve();
          }
          return;
        }
        if (event.type === "voice.speech.started") {
          handleSpeechStarted();
          return;
        }
        if (event.type === "voice.speech.stopped") {
          handleSpeechStopped();
          return;
        }
        if (event.type === "voice.transcript.delta") {
          if (event.text) elements.liveTranscript.textContent = event.text;
          return;
        }
        if (event.type === "voice.transcript.completed") {
          handleCompletedTranscript(event.text || "");
          return;
        }
        if (event.type === "voice.error") {
          const message = event.error?.message || "The speech service returned an error.";
          if (!settled) {
            settled = true;
            window.clearTimeout(timeout);
            reject(new Error(message));
          } else {
            showToast(message);
          }
        }
      });
      socket.addEventListener("error", () => {
        if (!settled) {
          settled = true;
          window.clearTimeout(timeout);
          reject(new Error("Could not connect to the speech service."));
        }
      });
      socket.addEventListener("close", () => {
        state.socketReady = false;
        state.recording = false;
        elements.recordButton.disabled = true;
        stopCapture().catch(() => {});
        if (!settled) {
          settled = true;
          window.clearTimeout(timeout);
          reject(new Error("The speech connection closed during setup."));
        } else if (state.stage === "interview") {
          setThread("error", "Speech connection closed", "Typed answers remain available");
        }
      });
    });
  }

  function sendRealtime(event) {
    if (state.socket?.readyState !== WebSocket.OPEN) return false;
    state.socket.send(JSON.stringify(event));
    return true;
  }

  async function toggleRecording() {
    if (state.recording) await finishRecording();
    else await startRecording();
  }

  async function startRecording() {
    if (!state.socketReady || state.awaitingTranscript) return;
    stopSpeech();
    elements.recordButton.disabled = true;
    elements.typedAnswerForm.hidden = true;
    state.transcriptSubmitted = false;
    state.awaitingTranscript = false;
    state.speechDetected = false;
    sendRealtime({ type: "audio.clear" });
    try {
      state.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false
      });
      const context = new AudioContext({ latencyHint: "interactive" });
      await context.audioWorklet.addModule("/static/pcm-worklet.js");
      await context.resume();
      state.audioContext = context;
      state.source = context.createMediaStreamSource(state.mediaStream);
      state.worklet = new AudioWorkletNode(context, "pcm-capture", {
        processorOptions: { targetRate: 24000, chunkMs: 100 }
      });
      state.silentGain = context.createGain();
      state.silentGain.gain.value = 0;
      state.source.connect(state.worklet);
      state.worklet.connect(state.silentGain).connect(context.destination);
      state.worklet.port.onmessage = ({ data }) => {
        if (data?.type === "flushed") {
          state.flushResolve?.();
          state.flushResolve = null;
          return;
        }
        sendPcm(data);
      };
      state.recording = true;
      state.answerStartedAt = performance.now();
      elements.recordButton.classList.add("is-recording");
      elements.recordLabel.textContent = "Done speaking";
      elements.recordButton.disabled = false;
      elements.liveTranscript.textContent = "Listening automatically… start speaking when you’re ready.";
      elements.replayButton.disabled = false;
      setThread("listening", "Your microphone is live", "Silence will end the turn automatically");
      startAnswerClock();
      scheduleTurnTimers();
    } catch (error) {
      await stopCapture();
      state.recording = false;
      elements.recordButton.classList.remove("is-recording");
      elements.recordLabel.textContent = "Retry voice";
      elements.recordButton.disabled = !state.socketReady;
      showToast(`Microphone capture failed: ${error.message}`);
      showTypedAnswer();
    }
  }

  async function finishRecording() {
    if (!state.recording) return;
    await flushWorklet();
    if (!state.recording || state.awaitingTranscript) return;
    state.recording = false;
    state.awaitingTranscript = true;
    sendRealtime({ type: "audio.commit" });
    await stopCapture();
    showTranscribingState();
  }

  function handleSpeechStarted() {
    if (!state.recording || state.awaitingTranscript) return;
    state.speechDetected = true;
    window.clearTimeout(state.silenceNudgeTimeout);
    elements.liveTranscript.textContent = "Speech detected… continue naturally.";
    setThread("listening", "Listening to your answer", "A short silence will complete the turn");
  }

  async function handleSpeechStopped() {
    if (!state.recording || state.awaitingTranscript) return;
    state.recording = false;
    state.awaitingTranscript = true;
    await stopCapture();
    showTranscribingState();
  }

  function showTranscribingState() {
    elements.recordButton.classList.remove("is-recording");
    elements.recordLabel.textContent = "Done speaking";
    elements.recordButton.disabled = true;
    elements.replayButton.disabled = false;
    stopAnswerClock();
    elements.liveTranscript.textContent = "Transcribing your answer…";
    setThread("thinking", "Transcribing response", "Finishing the transcript of your answer");
    window.clearTimeout(state.transcriptTimeout);
    state.transcriptTimeout = window.setTimeout(() => {
      if (!state.awaitingTranscript) return;
      elements.liveTranscript.textContent = "The transcript is taking longer than expected. You can type the answer instead.";
      showTypedAnswer();
    }, 20000);
  }

  function sendPcm(buffer) {
    if (!(buffer instanceof ArrayBuffer) || !state.recording) return;
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const block = 0x8000;
    for (let start = 0; start < bytes.length; start += block) {
      binary += String.fromCharCode(...bytes.subarray(start, start + block));
    }
    sendRealtime({ type: "audio.append", audio: btoa(binary) });
  }

  async function flushWorklet() {
    if (!state.worklet) return;
    await new Promise((resolve) => {
      const timeout = window.setTimeout(resolve, 300);
      state.flushResolve = () => {
        window.clearTimeout(timeout);
        resolve();
      };
      state.worklet.port.postMessage({ type: "flush" });
    });
    state.flushResolve = null;
  }

  async function stopCapture() {
    state.mediaStream?.getTracks().forEach((track) => track.stop());
    state.mediaStream = null;
    state.worklet?.disconnect();
    state.source?.disconnect();
    state.silentGain?.disconnect();
    if (state.audioContext && state.audioContext.state !== "closed") await state.audioContext.close();
    state.audioContext = null;
    state.worklet = null;
    state.source = null;
    state.silentGain = null;
  }

  function scheduleTurnTimers() {
    window.clearTimeout(state.silenceNudgeTimeout);
    window.clearTimeout(state.answerDeadlineTimeout);
    if (!state.questionDeadlineAt) state.questionDeadlineAt = Date.now() + ANSWER_LIMIT_MS;
    const remaining = Math.max(0, state.questionDeadlineAt - Date.now());
    if (!state.silenceNudged) {
      state.silenceNudgeTimeout = window.setTimeout(playSilenceNudge, Math.min(SILENCE_NUDGE_MS, remaining));
    }
    state.answerDeadlineTimeout = window.setTimeout(handleAnswerDeadline, remaining);
  }

  function clearTurnTimers() {
    window.clearTimeout(state.silenceNudgeTimeout);
    window.clearTimeout(state.answerDeadlineTimeout);
    state.silenceNudgeTimeout = 0;
    state.answerDeadlineTimeout = 0;
  }

  async function playSilenceNudge() {
    if (!state.recording || state.speechDetected || state.silenceNudged) return;
    state.silenceNudged = true;
    state.recording = false;
    sendRealtime({ type: "audio.clear" });
    await stopCapture();
    stopAnswerClock();
    setThread("speaking", "Interviewer checking in", "You still have time to answer");
    try {
      const response = await api(
        `/interviews/${state.intake.interview_id}/speech?output_format=wav&cue=silence_nudge`,
        { method: "POST", headers: { "Idempotency-Key": `silence-nudge-${state.currentQuestionIndex}` } }
      );
      const audio = new Audio(URL.createObjectURL(await response.blob()));
      await new Promise((resolve) => {
        audio.addEventListener("ended", resolve, { once: true });
        audio.addEventListener("error", resolve, { once: true });
        audio.play().catch(resolve);
      });
      URL.revokeObjectURL(audio.src);
    } catch (_) {
      showToast("Take your time. Begin when you're ready, or ask to move on.");
    }
    if (state.stage === "interview" && Date.now() < state.questionDeadlineAt) {
      await startRecording();
    }
  }

  async function handleAnswerDeadline() {
    if (state.stage !== "interview" || state.transcriptSubmitted) return;
    clearTurnTimers();
    state.recording = false;
    state.awaitingTranscript = false;
    sendRealtime({ type: "audio.clear" });
    await stopCapture();
    stopAnswerClock();
    showToast("The answer time has ended. Moving to the next question.");
    await submitAnswer("[answer time expired]", false);
  }

  async function handleCompletedTranscript(transcript) {
    const clean = transcript.trim();
    if (!state.awaitingTranscript || state.transcriptSubmitted) return;
    state.awaitingTranscript = false;
    state.transcriptSubmitted = true;
    window.clearTimeout(state.transcriptTimeout);
    if (!clean) {
      elements.liveTranscript.textContent = "No speech was detected. Listening again…";
      showToast("No speech was detected. You can speak again or type your answer.");
      await startRecording();
      return;
    }
    elements.liveTranscript.textContent = clean;
    await submitAnswer(clean);
  }

  async function submitAnswer(text, includeCandidateTranscript = true) {
    clearTurnTimers();
    elements.recordButton.disabled = true;
    elements.typeFallbackButton.disabled = true;
    setThread("thinking", "Reviewing this turn", "Preparing the next structured question");
    try {
      const response = await api(`/interviews/${state.intake.interview_id}/answers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      });
      const interview = await response.json();
      state.answers = interview.answers?.length || state.answers + 1;
      if (includeCandidateTranscript) appendConversation("You", text);
      elements.typedAnswerForm.hidden = true;
      elements.typedAnswer.value = "";
      elements.typeFallbackButton.disabled = false;
      if (interview.status === "completed") {
        completeInterview();
        return;
      }
      state.currentQuestionIndex = interview.current_question_index || state.currentQuestionIndex;
      state.currentQuestion = interview.last_response;
      state.questionDeadlineAt = 0;
      state.silenceNudged = false;
      elements.questionText.textContent = state.currentQuestion;
      elements.liveTranscript.textContent = "When you’re ready, start your answer.";
      updateQuestionCounter();
      appendConversation("Interviewer", state.currentQuestion);
      await playCurrentQuestion();
    } catch (error) {
      state.transcriptSubmitted = false;
      elements.recordLabel.textContent = "Retry voice";
      elements.recordButton.disabled = !state.socketReady;
      elements.typeFallbackButton.disabled = false;
      setThread("error", "Answer was not submitted", "Your transcript remains visible; try again");
      showToast(error.message);
    }
  }

  async function playCurrentQuestion() {
    stopSpeech();
    elements.replayButton.hidden = true;
    setThread("thinking", "Preparing interviewer audio", "The interviewer will speak in a moment");
    try {
      const response = await api(`/interviews/${state.intake.interview_id}/speech?output_format=wav`, {
        method: "POST",
        headers: { "Idempotency-Key": `speech-${state.currentQuestionIndex}-${Date.now()}` }
      });
      const blob = await response.blob();
      state.speechUrl = URL.createObjectURL(blob);
      state.speechAudio = new Audio(state.speechUrl);
      state.speechAudio.addEventListener("play", () => {
        setThread("speaking", "Interviewer speaking", "Listen to the complete question");
        elements.recordButton.disabled = true;
      });
      state.speechAudio.addEventListener("ended", () => {
        elements.replayButton.hidden = false;
        beginCandidateTurn();
      }, { once: true });
      await state.speechAudio.play();
    } catch (error) {
      setThread("ready", "Question shown on screen", "Audio playback is unavailable; you may still answer");
      elements.replayButton.hidden = false;
      showToast(`Question audio was unavailable: ${error.message}`);
      await beginCandidateTurn();
    }
  }

  async function beginCandidateTurn() {
    if (!state.socketReady) {
      elements.recordButton.disabled = true;
      showTypedAnswer();
      return;
    }
    await startRecording();
  }

  async function replayCurrentQuestion() {
    if (state.recording) {
      state.recording = false;
      sendRealtime({ type: "audio.clear" });
      await stopCapture();
      elements.recordButton.classList.remove("is-recording");
      stopAnswerClock();
    }
    await playCurrentQuestion();
  }

  function stopSpeech() {
    if (state.speechAudio) {
      state.speechAudio.pause();
      state.speechAudio.src = "";
    }
    if (state.speechUrl) URL.revokeObjectURL(state.speechUrl);
    state.speechAudio = null;
    state.speechUrl = null;
  }

  function updateQuestionCounter() {
    const number = Math.min(state.questionTotal, state.currentQuestionIndex + 1);
    elements.questionCounter.textContent = `Question ${number} of ${state.questionTotal}`;
  }

  function appendConversation(speaker, text) {
    const item = document.createElement("li");
    item.className = "conversation-item";
    const label = document.createElement("strong");
    label.textContent = speaker;
    const copy = document.createElement("p");
    copy.textContent = text;
    item.append(label, copy);
    elements.conversationLog.append(item);
  }

  async function showTypedAnswer() {
    window.clearTimeout(state.transcriptTimeout);
    state.awaitingTranscript = false;
    if (state.recording) {
      state.recording = false;
      sendRealtime({ type: "audio.clear" });
      await stopCapture();
      elements.recordButton.classList.remove("is-recording");
      stopAnswerClock();
    }
    elements.recordLabel.textContent = "Retry voice";
    elements.recordButton.disabled = !state.socketReady;
    setThread("ready", "Typed answer selected", "Submit your response when ready");
    elements.typedAnswerForm.hidden = false;
    elements.typedAnswer.focus();
  }

  async function cancelTypedAnswer() {
    elements.typedAnswerForm.hidden = true;
    await beginCandidateTurn();
  }

  async function submitTypedAnswer(event) {
    event.preventDefault();
    const text = elements.typedAnswer.value.trim();
    if (!text) return;
    window.clearTimeout(state.transcriptTimeout);
    state.awaitingTranscript = false;
    if (state.recording) {
      state.recording = false;
      await stopCapture();
      elements.recordButton.classList.remove("is-recording");
      elements.recordLabel.textContent = "Retry voice";
      stopAnswerClock();
    }
    await submitAnswer(text);
  }

  function completeInterview() {
    stopSpeech();
    state.recording = false;
    stopCapture().catch(() => {});
    stopAnswerClock();
    clearTurnTimers();
    window.clearInterval(state.elapsedClock);
    state.socket?.close(1000, "interview complete");
    elements.completedRole.textContent = state.intake.job.title;
    elements.completedAnswers.textContent = String(state.answers);
    setSystem("Interview complete", "Responses saved", "ready");
    setStage("complete");
  }

  function startSessionClock() {
    window.clearInterval(state.elapsedClock);
    const update = () => { elements.elapsedTime.textContent = formatDuration(performance.now() - state.sessionStartedAt); };
    update();
    state.elapsedClock = window.setInterval(update, 1000);
  }

  function startAnswerClock() {
    window.clearInterval(state.answerClock);
    const update = () => { elements.answerTimer.textContent = formatDuration(performance.now() - state.answerStartedAt); };
    update();
    state.answerClock = window.setInterval(update, 250);
  }

  function stopAnswerClock() {
    window.clearInterval(state.answerClock);
    state.answerClock = 0;
    elements.answerTimer.textContent = "Ready";
  }

  function formatDuration(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function formatBytes(value) {
    if (value < 1024) return `${value} B`;
    return `${(value / 1024 / 1024).toFixed(2)} MB`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"
    })[character]);
  }

  elements.jobSelect.addEventListener("change", () => { renderJob(); updatePrepareAvailability(); });
  elements.resumeInput.addEventListener("change", () => selectResume(elements.resumeInput.files[0]));
  elements.uploadZone.addEventListener("dragover", (event) => { event.preventDefault(); elements.uploadZone.classList.add("is-dragging"); });
  elements.uploadZone.addEventListener("dragleave", () => elements.uploadZone.classList.remove("is-dragging"));
  elements.uploadZone.addEventListener("drop", (event) => {
    event.preventDefault();
    elements.uploadZone.classList.remove("is-dragging");
    selectResume(event.dataTransfer.files[0]);
  });
  elements.intakeForm.addEventListener("submit", prepareInterview);
  elements.checkMicButton.addEventListener("click", checkMicrophone);
  elements.cameraButton.addEventListener("click", toggleCamera);
  elements.planningAcknowledgement.addEventListener("change", () => {
    state.planningAcknowledged = elements.planningAcknowledgement.checked;
    updateBeginAvailability();
  });
  elements.backButton.addEventListener("click", async () => {
    state.intakeRequestId += 1;
    state.intakePending = false;
    state.intake = null;
    await stopMicPreview();
    stopCamera();
    updatePrepareAvailability();
    setStage("upload");
  });
  elements.beginButton.addEventListener("click", beginInterview);
  elements.recordButton.addEventListener("click", toggleRecording);
  elements.replayButton.addEventListener("click", replayCurrentQuestion);
  elements.typeFallbackButton.addEventListener("click", showTypedAnswer);
  elements.cancelTypedButton.addEventListener("click", cancelTypedAnswer);
  elements.typedAnswerForm.addEventListener("submit", submitTypedAnswer);
  window.addEventListener("beforeunload", () => {
    state.socket?.close();
    state.mediaStream?.getTracks().forEach((track) => track.stop());
    state.micPreviewStream?.getTracks().forEach((track) => track.stop());
    state.cameraStream?.getTracks().forEach((track) => track.stop());
    stopSpeech();
  });

  loadJobs();
})();
