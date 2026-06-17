(function () {
    'use strict';

    console.log('[ECHOES APP.JS] IIFE started — DOM ready, binding handlers');

    var stageRoot = document.getElementById('stage-root');
    var stageAgenticPanel = document.getElementById('stage-agentic-panel');
    var stageBackground = document.getElementById('stage-background');
    var video = document.getElementById('pet-video');
    var panelVideo = document.getElementById('panel-video');
    var character = document.getElementById('pet-character');
    var audio = document.getElementById('room-audio');
    var roomCharacterName = document.getElementById('room-character-name');
    var runtimeModeBadge = document.getElementById('runtime-mode-badge');
    var actionStatus = document.getElementById('action-status');
    var actionStatusText = document.getElementById('action-status-text');
    var conversationList = document.getElementById('conversation-list');
    var conversationQueueText = document.getElementById('conversation-queue-text');
    var xpDisplay = document.getElementById('xp-display');
    var xpProgressBar = document.getElementById('xp-progress-bar');
    var xpThresholdDisplay = document.getElementById('xp-threshold-display');
    var providerSummary = document.getElementById('provider-summary');
    var resultReply = document.getElementById('result-reply');
    var resultSkill = document.getElementById('result-skill');
    var resultTool = document.getElementById('result-tool');
    var resultXpDelta = document.getElementById('result-xp-delta');
    var resultReward = document.getElementById('result-reward');
    var resultAsset = document.getElementById('result-asset');
    var resultBehavior = document.getElementById('result-behavior');
    var resultWebmKey = document.getElementById('result-webm-key');
    var resultSaved = document.getElementById('result-saved');
    var warningsList = document.getElementById('warnings-list');
    var skillList = document.getElementById('skill-list');
    var toolList = document.getElementById('tool-list');
    var skillCountBadge = document.getElementById('skill-count-badge');
    var toolCountBadge = document.getElementById('tool-count-badge');
    var interactionInput = document.getElementById('interaction-input');
    var liveConversationInput = document.getElementById('live-conversation-input');
    var providerSelect = document.getElementById('provider-select');
    var sendButton = document.getElementById('send-button');
    var liveSendButton = document.getElementById('live-send-button');
    var refreshStateButton = document.getElementById('refresh-state-button');
    var refreshSkillsButton = document.getElementById('refresh-skills-button');
    var refreshToolsButton = document.getElementById('refresh-tools-button');
    var skillForm = document.getElementById('skill-form');
    var toolForm = document.getElementById('tool-form');
    var bridgeStatusEl = document.getElementById('bridge-status');
    var lastActionEl = document.getElementById('last-action');
    var lastErrorEl = document.getElementById('last-error');
    var backgroundStatusEl = document.getElementById('background-status');
    var micButton = document.getElementById('mic-button');
    var speakReplyButton = document.getElementById('speak-reply-button');
    var voiceStatus = document.getElementById('voice-status');
    var voiceSttStatus = document.getElementById('voice-stt-status');
    var voiceTtsStatus = document.getElementById('voice-tts-status');
    var voiceTtsFallbackStatus = document.getElementById('voice-tts-fallback-status');
    var voiceAudioWorkerStatus = document.getElementById('voice-audio-worker-status');
    var voiceLastError = document.getElementById('voice-last-error');
    var panelToggleButton = document.getElementById('agentic-panel-toggle');

    var diagProviderSelected = document.getElementById('diag-provider-selected');
    var diagProviderResolved = document.getElementById('diag-provider-resolved');
    var diagApiConfigStatus = document.getElementById('diag-api-config-status');
    var diagSkillCount = document.getElementById('diag-skill-count');
    var diagMatchedSkill = document.getElementById('diag-matched-skill');
    var diagToolCount = document.getElementById('diag-tool-count');
    var diagToolStatus = document.getElementById('diag-tool-status');
    var diagXpTotal = document.getElementById('diag-xp-total');
    var diagLevel = document.getElementById('diag-level');
    var diagNextLevelXp = document.getElementById('diag-next-level-xp');
    var diagRewardCount = document.getElementById('diag-reward-count');
    var diagAssetManifestCount = document.getElementById('diag-asset-manifest-count');
    var diagBehaviorId = document.getElementById('diag-behavior-id');
    var diagWebmKey = document.getElementById('diag-webm-key');
    var diagBackgroundStatus = document.getElementById('diag-background-status');
    var diagVoiceSttStatus = document.getElementById('diag-voice-stt-status');
    var diagVoiceTtsStatus = document.getElementById('diag-voice-tts-status');
    var diagVoiceTtsFallbackStatus = document.getElementById('diag-voice-tts-fallback-status');
    var diagVoiceAudioWorkerStatus = document.getElementById('diag-voice-audio-worker-status');
    var diagVoiceLastError = document.getElementById('diag-voice-last-error');
    var diagRuntimeBrainMode = document.getElementById('diag-runtime-brain-mode');
    var diagLiveRuntimeAvailable = document.getElementById('diag-live-runtime-available');
    var diagHarnessRuntimeAvailable = document.getElementById('diag-harness-runtime-available');
    var diagOpenclawEnabled = document.getElementById('diag-openclaw-enabled');
    var diagStageSize = document.getElementById('diag-stage-size');
    var diagStageScale = document.getElementById('diag-stage-scale');
    var diagPetAnchorX = document.getElementById('diag-pet-anchor-x');
    var diagPetAnchorY = document.getElementById('diag-pet-anchor-y');
    var diagPetScale = document.getElementById('diag-pet-scale');
    var diagIdleMotionCandidatesCount = document.getElementById('diag-idle-motion-candidates-count');
    var diagBridgeReady = document.getElementById('diag-bridge-ready');

    var securityFields = {
        OPENAI_API_KEY: document.getElementById('diag-security-openai-api-key'),
        CHATGPT_API_KEY: document.getElementById('diag-security-chatgpt-api-key'),
        AZURE_STT_API_KEY: document.getElementById('diag-security-azure-stt-api-key'),
        AZURE_STT_REGION: document.getElementById('diag-security-azure-stt-region'),
        ELEVENLABS_API_KEY: document.getElementById('diag-security-elevenlabs-api-key'),
        ELEVENLABS_MODEL_ID: document.getElementById('diag-security-elevenlabs-model-id')
    };

    var idleSource = '';
    var idleMotionCandidates = [];
    var idleMotionIndex = 0;
    var statusTimer = null;
    var defaultStatusText = 'Waiting for room updates.';
    var harnessBridge = null;
    var agenticBusy = false;
    var latestVoiceState = null;
    var latestReplyText = '';
    var motionLoopTimer = null;
    var motionLoopSource = null;
    var motionLoopActive = false;
    var motionLoopGeneration = 0;
    var panelVideoGeneration = 0;
    var conversationTurns = new Map();
    var maxConversationTurns = 3;
    var latestRuntimeState = null;
    var resizeObserver = null;

    window.echoes = window.echoes || {};

    function setText(element, value, fallback) {
        if (!element) return;
        element.textContent = value == null || value === '' ? (fallback || '-') : String(value);
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function setDiagBridgeStatus(text, isReady) {
        if (!bridgeStatusEl) return;
        bridgeStatusEl.textContent = 'Bridge: ' + text;
        bridgeStatusEl.dataset.ready = isReady ? 'true' : 'false';
        setText(diagBridgeReady, isReady ? 'true' : 'false');
    }

    function setDiagLastAction(text) {
        if (!lastActionEl) return;
        lastActionEl.textContent = 'Last: ' + text;
    }

    function setDiagLastError(text) {
        if (!lastErrorEl) return;
        lastErrorEl.textContent = text ? ('Error: ' + text) : '';
        lastErrorEl.style.display = text ? 'block' : 'none';
    }

    function setStatus(message, tone, timeoutMs) {
        if (statusTimer) {
            clearTimeout(statusTimer);
            statusTimer = null;
        }
        if (!actionStatus) {
            return;
        }
        actionStatus.dataset.tone = tone || 'idle';
        actionStatusText.textContent = message || defaultStatusText;
        if (timeoutMs && timeoutMs > 0) {
            statusTimer = window.setTimeout(function () {
                window.clearActionStatus();
            }, timeoutMs);
        }
    }

    function updateStageScale() {
        var viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1;
        var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 1;
        var scale = Math.min(viewportWidth / 2560, viewportHeight / 1440);
        document.documentElement.style.setProperty('--stage-scale', String(scale));
        renderStageDiagnostics();
    }

    function getComputedCssValue(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function renderStageDiagnostics() {
        var rect = stageRoot ? stageRoot.getBoundingClientRect() : { width: 0, height: 0 };
        setText(diagStageSize, Math.round(rect.width) + 'x' + Math.round(rect.height));
        setText(diagStageScale, getComputedCssValue('--stage-scale'));
        setText(diagPetAnchorX, getComputedCssValue('--pet-anchor-x'));
        setText(diagPetAnchorY, getComputedCssValue('--pet-floor-y'));
        setText(diagPetScale, getComputedCssValue('--pet-scale'));
        setText(diagIdleMotionCandidatesCount, idleMotionCandidates.length, '0');

        var petLayer = document.getElementById('stage-pet-layer');
        var petLayerRect = petLayer ? petLayer.getBoundingClientRect() : null;
        var vr = video ? video.getBoundingClientRect() : null;
        var agenticPanel = document.getElementById('agentic-panel');
        var panelWidth = agenticPanel ? Math.round(agenticPanel.getBoundingClientRect().width) : 0;
        var videoLoaded = video && video.src && video.src !== '' && video.src !== window.location.href;

        var stageWidth = Math.round(rect.width);
        var stageHeight = Math.round(rect.height);
        var videoWidth = vr ? Math.round(vr.width) : 0;
        var videoX = vr ? Math.round(vr.left) : 0;
        var videoY = vr ? Math.round(vr.top) : 0;
        var expectedCenteredX = (stageWidth > 0 && videoWidth > 0)
            ? Math.round((stageWidth - videoWidth) / 2) : 0;
        var centeredDeltaX = videoX - expectedCenteredX;
        var visible = vr
            ? (vr.right > 0 && vr.left < stageWidth && vr.bottom > 0 && vr.top < stageHeight)
            : false;

        console.log(
            '[ECHOES STAGE DIAG]',
            'stage=' + stageWidth + 'x' + stageHeight,
            'petLayer=' + (petLayerRect ? Math.round(petLayerRect.width) + 'x' + Math.round(petLayerRect.height) : 'null'),
            'videoRect=' + (vr ? videoX + ',' + videoY + ' ' + videoWidth + 'x' + Math.round(vr.height) : 'null'),
            'anchor=' + getComputedCssValue('--pet-anchor-x'),
            'scale=' + getComputedCssValue('--pet-scale'),
            'panelW=' + panelWidth,
            'videoLoaded=' + videoLoaded,
            'visible=' + visible,
            'centeredDeltaX=' + centeredDeltaX
        );
    }

    window.echoes.debugStageRects = function () {
        var chain = [
            { label: '#stage-root', el: document.getElementById('stage-root') },
            { label: '.room-scene', el: document.querySelector('.room-scene') },
            { label: '#stage-pet-layer', el: document.getElementById('stage-pet-layer') },
            { label: '#pet-stage-anchor', el: document.getElementById('pet-stage-anchor') },
            { label: '#pet-character', el: document.getElementById('pet-character') },
            { label: '#pet-video', el: document.getElementById('pet-video') }
        ];
        chain.forEach(function (item) {
            var el = item.el;
            if (!el) {
                console.log('[ECHOES RECTS] ' + item.label + ' = NOT FOUND');
                return;
            }
            var r = el.getBoundingClientRect();
            var cs = window.getComputedStyle(el);
            console.log(
                '[ECHOES RECTS]', item.label,
                'rect=' + Math.round(r.left) + ',' + Math.round(r.top) +
                    ' ' + Math.round(r.width) + 'x' + Math.round(r.height),
                'pos=' + cs.position,
                'left=' + cs.left,
                'bottom=' + cs.bottom,
                'w=' + cs.width,
                'h=' + cs.height,
                'transform=' + cs.transform
            );
        });
    };

    function syncAgenticPanelOffset() {
        var collapsed = document.body.dataset.agenticPanel === 'collapsed';
        var panelWidth = 0;
        if (!collapsed) {
            var panel = document.getElementById('agentic-panel');
            panelWidth = panel ? Math.round(panel.getBoundingClientRect().width) : 0;
        }
        document.documentElement.style.setProperty('--agentic-panel-offset', panelWidth + 'px');
        if (panelToggleButton) {
            panelToggleButton.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        }
        renderStageDiagnostics();
    }

    function toggleAgenticPanel() {
        var collapsed = document.body.dataset.agenticPanel === 'collapsed';
        document.body.dataset.agenticPanel = collapsed ? 'expanded' : 'collapsed';
        syncAgenticPanelOffset();
    }

    function normalizeIdleMotionCandidates(candidates) {
        if (!Array.isArray(candidates)) {
            return [];
        }
        return candidates.map(function (candidate) {
            if (typeof candidate === 'string') {
                return { source: candidate, weight: 1 };
            }
            if (!candidate || !candidate.source) {
                return null;
            }
            return {
                source: String(candidate.source),
                weight: Math.max(1, Number(candidate.weight || 1))
            };
        }).filter(Boolean);
    }

    function nextIdleCandidateSource(fallbackSource) {
        if (!idleMotionCandidates.length) {
            return fallbackSource || idleSource;
        }
        var candidate = idleMotionCandidates[idleMotionIndex % idleMotionCandidates.length];
        idleMotionIndex = (idleMotionIndex + 1) % idleMotionCandidates.length;
        return candidate.source;
    }

    function setSource(source, shouldLoop) {
        if (!source || typeof source !== 'string') {
            console.warn('[ECHOES] invalid video source', source);
            return;
        }
        video.loop = Boolean(shouldLoop);
        video.src = source;
        video.load();
        video.play().catch(function (err) {
            console.warn('[ECHOES] video playback failed:', err.message);
        });
    }

    function trimConversationTurns() {
        while (conversationList && conversationList.children.length > maxConversationTurns) {
            var firstChild = conversationList.firstElementChild;
            if (!firstChild) {
                break;
            }
            conversationTurns.delete(firstChild.dataset.turnId);
            conversationList.removeChild(firstChild);
        }
    }

    function ensureConversationTurn(turnId, sourceLabel) {
        var existing = conversationTurns.get(turnId);
        if (existing) {
            return existing;
        }
        var article = document.createElement('article');
        article.className = 'conversation-turn';
        article.dataset.turnId = turnId;
        article.dataset.state = 'active';

        var userRow = document.createElement('div');
        userRow.className = 'conversation-turn__row';
        var userLabel = document.createElement('p');
        userLabel.className = 'conversation-turn__label';
        userLabel.textContent = sourceLabel || 'User';
        var userCopy = document.createElement('div');
        userCopy.className = 'conversation-turn__copy';
        var userText = document.createElement('p');
        userText.className = 'conversation-turn__text';
        userCopy.appendChild(userText);
        userRow.appendChild(userLabel);
        userRow.appendChild(userCopy);

        var assistantRow = document.createElement('div');
        assistantRow.className = 'conversation-turn__row';
        var assistantLabel = document.createElement('p');
        assistantLabel.className = 'conversation-turn__label';
        assistantLabel.textContent = 'Echoes';
        var assistantCopy = document.createElement('div');
        assistantCopy.className = 'conversation-turn__copy';
        var assistantText = document.createElement('p');
        assistantText.className = 'conversation-turn__text conversation-turn__text--muted';
        assistantCopy.appendChild(assistantText);
        assistantRow.appendChild(assistantLabel);
        assistantRow.appendChild(assistantCopy);

        article.appendChild(userRow);
        article.appendChild(assistantRow);
        if (conversationList) {
            conversationList.appendChild(article);
        }
        var turn = {
            root: article,
            userText: userText,
            assistantText: assistantText
        };
        conversationTurns.set(turnId, turn);
        trimConversationTurns();
        return turn;
    }

    function renderWarnings(warnings) {
        var entries = Array.isArray(warnings) && warnings.length ? warnings : ['None'];
        warningsList.innerHTML = entries.map(function (item) {
            return '<li>' + escapeHtml(item) + '</li>';
        }).join('');
    }

    function renderSkills(skills) {
        var items = Array.isArray(skills) ? skills : [];
        skillCountBadge.textContent = items.length + ' skills';
        if (!items.length) {
            skillList.innerHTML = '<div class="entity-card"><p class="entity-card__title">No skills loaded.</p></div>';
            return;
        }
        skillList.innerHTML = items.map(function (item) {
            var toggleLabel = item.enabled ? 'Disable' : 'Enable';
            var deleteLabel = item.is_builtin ? 'Disable only' : 'Delete';
            return [
                '<article class="entity-card">',
                '  <div class="entity-card__head">',
                '    <div>',
                '      <p class="entity-card__title">' + escapeHtml(item.display_name || item.skill_id) + '</p>',
                '      <p class="entity-card__meta">' + escapeHtml(item.skill_id) + '</p>',
                '    </div>',
                '    <span class="status-pill">' + (item.enabled ? 'enabled' : 'disabled') + '</span>',
                '  </div>',
                '  <p class="entity-card__meta">' + escapeHtml(item.description || '-') + '</p>',
                '  <p class="entity-card__meta">triggers: ' + escapeHtml((item.triggers || []).join(', ') || '-') + '</p>',
                '  <div class="entity-card__actions">',
                '    <button class="secondary-button" type="button" data-skill-toggle="' + escapeHtml(item.skill_id) + '" data-enabled="' + String(!item.enabled) + '">' + toggleLabel + '</button>',
                '    <button class="danger-button" type="button" data-skill-delete="' + escapeHtml(item.skill_id) + '">' + deleteLabel + '</button>',
                '  </div>',
                '</article>'
            ].join('');
        }).join('');
    }

    function renderTools(tools) {
        var items = Array.isArray(tools) ? tools : [];
        toolCountBadge.textContent = items.length + ' tools';
        if (!items.length) {
            toolList.innerHTML = '<div class="entity-card"><p class="entity-card__title">No tools loaded.</p></div>';
            return;
        }
        toolList.innerHTML = items.map(function (item) {
            var toggleLabel = item.enabled ? 'Disable' : 'Enable';
            var deleteLabel = item.is_builtin ? 'Disable only' : 'Delete config';
            return [
                '<article class="entity-card">',
                '  <div class="entity-card__head">',
                '    <div>',
                '      <p class="entity-card__title">' + escapeHtml(item.tool_name) + '</p>',
                '      <p class="entity-card__meta">' + escapeHtml(item.status || '-') + '</p>',
                '    </div>',
                '    <span class="status-pill">' + (item.enabled ? 'enabled' : 'disabled') + '</span>',
                '  </div>',
                '  <p class="entity-card__meta">' + escapeHtml(item.description || '-') + '</p>',
                '  <div class="entity-card__actions">',
                '    <button class="secondary-button" type="button" data-tool-toggle="' + escapeHtml(item.tool_name) + '" data-enabled="' + String(!item.enabled) + '">' + toggleLabel + '</button>',
                '    <button class="danger-button" type="button" data-tool-delete="' + escapeHtml(item.tool_name) + '">' + deleteLabel + '</button>',
                '  </div>',
                '</article>'
            ].join('');
        }).join('');
    }

    function renderEvent(event) {
        var payload = event || {};
        var tool = payload.tool || {};
        var asset = payload.asset_summary || {};
        latestReplyText = payload.reply || latestReplyText || '';
        setText(resultReply, payload.reply, 'Waiting for input.');
        setText(resultSkill, payload.matched_skill);
        setText(resultTool, tool.name ? [tool.name, tool.status || '-', tool.reason || ''].filter(Boolean).join(' | ') : '-', '-');
        setText(resultXpDelta, payload.xp_delta == null ? 0 : payload.xp_delta, '0');
        setText(resultReward, Array.isArray(payload.reward_summary) && payload.reward_summary.length ? payload.reward_summary.join(', ') : '-');
        setText(resultAsset, asset.asset_id || asset.webm_key || asset.status || '-');
        setText(resultBehavior, payload.behavior_id, 'idle');
        setText(resultWebmKey, payload.webm_key, 'idle');
        setText(resultSaved, String(Boolean(payload.saved_to_db)));
        if (payload.provider_status && payload.provider_status.provider_type) {
            providerSummary.textContent = 'provider: ' + payload.provider_status.provider_type;
        }
        renderWarnings(payload.warnings || []);
    }

    function renderXpState(xp) {
        if (!xp) return;
        setText(xpDisplay, xp.display);
        if (xpProgressBar) {
            var percent = Number(xp.progress_percent || 0);
            xpProgressBar.style.width = Math.max(0, Math.min(100, percent)) + '%';
        }
        setText(xpThresholdDisplay, String(xp.xp_total || 0) + ' / ' + String(xp.next_level_xp || 100) + ' XP');
    }

    function renderBackgroundStatus(background) {
        var status = background && background.status ? background.status : 'missing';
        setText(backgroundStatusEl, 'Background: ' + status);
        console.log('[ECHOES UI] background=' + status);
    }

    function renderVoiceStatus(voice) {
        latestVoiceState = voice || null;
        var stt = voice && voice.stt ? voice.stt : {};
        var tts = voice && voice.tts ? voice.tts : {};
        var ttsFallback = voice && voice.tts_fallback ? voice.tts_fallback : {};
        var audioWorker = voice && voice.audio_worker ? voice.audio_worker : {};
        setText(voiceStatus, 'voice: ' + (voice.overall_status || stt.status || 'missing'));
        setText(voiceSttStatus, stt.message || stt.status);
        setText(voiceTtsStatus, tts.message || tts.status);
        setText(voiceTtsFallbackStatus, ttsFallback.message || ttsFallback.status);
        setText(voiceAudioWorkerStatus, audioWorker.message || audioWorker.status);
        setText(voiceLastError, voice.last_voice_error || '-', '-');
        console.log('[ECHOES UI] voice.tts=configured status=' + (tts.status || 'missing'));
    }

    function renderSecuritySummary(security) {
        security = security || {};
        Object.keys(securityFields).forEach(function (key) {
            setText(securityFields[key], security[key], '[missing]');
        });
    }

    function renderDiagnostics(diagnostics) {
        diagnostics = diagnostics || {};
        setText(diagProviderSelected, diagnostics.provider_selected);
        setText(diagProviderResolved, diagnostics.provider_resolved);
        setText(diagApiConfigStatus, diagnostics.api_config_status);
        setText(diagSkillCount, diagnostics.skill_count, '0');
        setText(diagMatchedSkill, diagnostics.matched_skill);
        setText(diagToolCount, diagnostics.tool_count, '0');
        setText(diagToolStatus, diagnostics.tool_status);
        setText(diagXpTotal, diagnostics.xp_total, '0');
        setText(diagLevel, diagnostics.level, '1');
        setText(diagNextLevelXp, diagnostics.next_level_xp, '100');
        setText(diagRewardCount, diagnostics.reward_count, '0');
        setText(diagAssetManifestCount, diagnostics.asset_manifest_count, '0');
        setText(diagBehaviorId, diagnostics.behavior_id, 'idle');
        setText(diagWebmKey, diagnostics.webm_key, 'idle');
        setText(diagBackgroundStatus, diagnostics.background_status);
        setText(diagVoiceSttStatus, diagnostics.voice_stt_status);
        setText(diagVoiceTtsStatus, diagnostics.voice_tts_status);

        var runtime = diagnostics.runtime || {};
        setText(diagRuntimeBrainMode, runtime.brain_mode || diagnostics.brain_mode, 'harness');
        setText(diagLiveRuntimeAvailable, runtime.live_runtime_available);
        setText(diagHarnessRuntimeAvailable, runtime.harness_runtime_available);
        setText(diagOpenclawEnabled, runtime.openclaw_enabled);

        var voice = diagnostics.voice || {};
        setText(diagVoiceTtsFallbackStatus, voice.tts_fallback_status);
        setText(diagVoiceAudioWorkerStatus, voice.audio_worker_status);
        setText(diagVoiceLastError, voice.last_voice_error, '-');

        renderSecuritySummary(diagnostics.security || {});
        renderStageDiagnostics();
    }

    function renderState(state) {
        if (!state) return;
        latestRuntimeState = state;
        renderXpState(state.xp || null);
        if (state.provider_config && state.provider_config.provider_type) {
            providerSelect.value = state.provider_config.provider_type;
        }
        if (state.provider_status && state.provider_status.provider_type) {
            providerSummary.textContent = 'provider: ' + state.provider_status.provider_type;
        }
        renderBackgroundStatus(state.background || null);
        renderVoiceStatus(state.voice || null);
        renderDiagnostics(state.diagnostics || null);
    }

    function callBridge(method) {
        var args = Array.prototype.slice.call(arguments, 1);
        if (!harnessBridge || typeof harnessBridge[method] !== 'function') {
            var msg = 'Bridge not ready — cannot call: ' + method;
            console.warn('[ECHOES UI] ' + msg);
            setDiagLastError(msg);
            setStatus('Bridge unavailable. Check qwebchannel.js.', 'error', 0);
            return;
        }
        setDiagLastError('');
        return harnessBridge[method].apply(harnessBridge, args);
    }

    function routeConversationInput(inputEl) {
        var path = String(inputEl.getAttribute('data-conversation-path') || inputEl.dataset.conversationPath || '').trim().toLowerCase();
        if (!path) {
            return;
        }
        var text = String(inputEl.value || '').trim();
        if (!text) {
            setStatus('Please enter some text first.', 'warn', 2200);
            return;
        }
        setDiagLastAction('path=' + path);
        if (path === 'live') {
            console.log('[ECHOES UI] live send clicked, text:', text);
            callBridge('sendLiveText', text);
            inputEl.value = '';
            return;
        }
        if (agenticBusy) {
            setStatus('Interaction already running.', 'warn', 2200);
            return;
        }
        console.log('[ECHOES UI] action=send provider=' + providerSelect.value);
        console.log('[ECHOES UI] send clicked, text:', text, 'provider:', providerSelect.value);
        callBridge('sendText', text, providerSelect.value);
    }

    function triggerSend() {
        routeConversationInput(interactionInput);
    }

    function triggerLiveSend() {
        routeConversationInput(liveConversationInput);
    }

    function handleVoiceAction(kind) {
        var voice = latestVoiceState || {};
        var status = kind === 'tts' ? (voice.tts || {}) : (voice.stt || {});
        var label = kind === 'tts' ? 'TTS' : 'STT';
        var message = status.message || (label + ' unavailable.');
        console.log('[ECHOES UI] voice.' + kind + '=configured status=' + (status.status || 'missing'));
        setDiagLastAction('voice ' + kind);
        setStatus(message, status.implemented ? 'idle' : 'warn', 4200);
    }

    function wireScenarioButtons() {
        var grid = document.getElementById('scenario-grid');
        if (!grid) return;
        grid.addEventListener('click', function (event) {
            var button = event.target.closest('.scenario-button');
            if (!button) return;
            var text = button.dataset.text || '';
            interactionInput.value = text;
            console.log('[ECHOES UI] scenario clicked:', text);
            setDiagLastAction('scenario: ' + text);
        });
    }

    function wireDynamicActions() {
        skillList.addEventListener('click', function (event) {
            var toggle = event.target.closest('[data-skill-toggle]');
            var remove = event.target.closest('[data-skill-delete]');
            if (toggle) {
                var skillId = toggle.dataset.skillToggle;
                var enabled = toggle.dataset.enabled === 'true';
                console.log('[ECHOES UI] skill toggle clicked:', skillId, '->', enabled);
                callBridge('toggleSkill', skillId, enabled);
            } else if (remove) {
                var skillIdDel = remove.dataset.skillDelete;
                console.log('[ECHOES UI] skill delete clicked:', skillIdDel);
                callBridge('deleteSkill', skillIdDel);
            }
        });

        toolList.addEventListener('click', function (event) {
            var toggle = event.target.closest('[data-tool-toggle]');
            var remove = event.target.closest('[data-tool-delete]');
            if (toggle) {
                var toolName = toggle.dataset.toolToggle;
                var enabled = toggle.dataset.enabled === 'true';
                console.log('[ECHOES UI] tool toggle clicked:', toolName, '->', enabled);
                callBridge('toggleTool', toolName, enabled);
            } else if (remove) {
                var toolNameDel = remove.dataset.toolDelete;
                console.log('[ECHOES UI] tool delete clicked:', toolNameDel);
                callBridge('deleteToolConfig', toolNameDel);
            }
        });
    }

    function setupForms() {
        sendButton.addEventListener('click', function () {
            console.log('[ECHOES UI] send clicked');
            triggerSend();
        });
        liveSendButton.addEventListener('click', triggerLiveSend);
        if (panelToggleButton) {
            panelToggleButton.addEventListener('click', toggleAgenticPanel);
        }
        refreshStateButton.addEventListener('click', function () {
            console.log('[ECHOES UI] refresh state clicked');
            callBridge('refreshState');
        });
        refreshSkillsButton.addEventListener('click', function () {
            console.log('[ECHOES UI] refresh skills clicked');
            callBridge('refreshState');
        });
        refreshToolsButton.addEventListener('click', function () {
            console.log('[ECHOES UI] refresh tools clicked');
            callBridge('refreshState');
        });
        interactionInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                triggerSend();
            }
        });
        liveConversationInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                triggerLiveSend();
            }
        });
        skillForm.addEventListener('submit', function (event) {
            event.preventDefault();
            var payload = {
                skill_id: document.getElementById('skill-id').value.trim(),
                display_name: document.getElementById('skill-display-name').value.trim(),
                description: document.getElementById('skill-description').value.trim(),
                triggers: document.getElementById('skill-triggers').value.trim(),
                default_behavior: document.getElementById('skill-behavior').value.trim(),
                required_tool: document.getElementById('skill-required-tool').value.trim()
            };
            callBridge('addSkill', JSON.stringify(payload));
            skillForm.reset();
        });
        toolForm.addEventListener('submit', function (event) {
            event.preventDefault();
            var payload = {
                tool_name: document.getElementById('tool-name').value.trim(),
                description: document.getElementById('tool-description').value.trim(),
                risk_level: document.getElementById('tool-risk-level').value,
                enabled: document.getElementById('tool-enabled').checked
            };
            callBridge('addToolConfig', JSON.stringify(payload));
            toolForm.reset();
            document.getElementById('tool-enabled').checked = true;
            document.getElementById('tool-risk-level').value = 'low';
        });
        if (micButton) {
            micButton.addEventListener('click', function () {
                handleVoiceAction('stt');
            });
        }
        if (speakReplyButton) {
            speakReplyButton.addEventListener('click', function () {
                if (!latestReplyText) {
                    setStatus('No latest reply to speak.', 'warn', 2600);
                    return;
                }
                handleVoiceAction('tts');
            });
        }
    }

    function setupWebChannel() {
        setDiagBridgeStatus('initializing…', false);
        if (typeof window.QWebChannel === 'undefined') {
            var msg = 'QWebChannel not available (qwebchannel.js failed to load)';
            console.error('[ECHOES UI]', msg);
            setDiagBridgeStatus('not ready — ' + msg, false);
            setStatus('Bridge unavailable. Check qwebchannel.js.', 'error', 0);
            return;
        }
        if (!window.qt || !window.qt.webChannelTransport) {
            var msg2 = 'qt.webChannelTransport not available (not inside Qt WebEngine?)';
            console.error('[ECHOES UI]', msg2);
            setDiagBridgeStatus('not ready — ' + msg2, false);
            setStatus('Qt transport missing. Run via PyQt5.', 'error', 0);
            return;
        }
        new window.QWebChannel(window.qt.webChannelTransport, function (channel) {
            harnessBridge = channel.objects.harnessBridge || null;
            if (!harnessBridge) {
                var errMsg = 'harnessBridge object not found in channel.objects';
                console.error('[ECHOES UI]', errMsg);
                setDiagBridgeStatus('not ready — object missing', false);
                setStatus('Bridge object missing. Check PyQt registration.', 'error', 0);
                return;
            }
            console.log('[ECHOES UI] bridge=ready');
            setDiagBridgeStatus('ready', true);
            setStatus('Bridge ready.', 'idle', 1800);
            callBridge('refreshState');
        });
    }

    window.beginConversationTurn = function (turnId, sourceLabel, userText) {
        if (!turnId) return;
        var turn = ensureConversationTurn(String(turnId), sourceLabel || 'User');
        turn.root.dataset.state = 'active';
        turn.userText.textContent = userText || '';
        turn.assistantText.textContent = 'Waiting for reply...';
        turn.assistantText.classList.add('conversation-turn__text--muted');
    };

    window.appendConversationAssistant = function (turnId, fragment) {
        if (!turnId || !fragment) return;
        var turn = ensureConversationTurn(String(turnId), 'User');
        if (turn.assistantText.classList.contains('conversation-turn__text--muted')) {
            turn.assistantText.textContent = '';
            turn.assistantText.classList.remove('conversation-turn__text--muted');
        }
        turn.assistantText.textContent += String(fragment);
    };

    window.setConversationAssistant = function (turnId, message) {
        if (!turnId) return;
        var turn = ensureConversationTurn(String(turnId), 'User');
        turn.assistantText.textContent = String(message || '') || 'No visible reply.';
        if (turn.assistantText.textContent === 'No visible reply.') {
            turn.assistantText.classList.add('conversation-turn__text--muted');
        } else {
            turn.assistantText.classList.remove('conversation-turn__text--muted');
        }
    };

    window.finishConversationTurn = function (turnId) {
        if (!turnId) return;
        var turn = ensureConversationTurn(String(turnId), 'User');
        turn.root.dataset.state = 'done';
        if (!turn.assistantText.textContent) {
            turn.assistantText.textContent = 'No visible reply.';
            turn.assistantText.classList.add('conversation-turn__text--muted');
        }
    };

    window.setConversationQueueDepth = function (queueDepth) {
        var depth = Number(queueDepth) || 0;
        setText(conversationQueueText, 'Queue ' + depth);
    };

    window.clearConversationTurns = function () {
        conversationTurns.clear();
        while (conversationList && conversationList.firstElementChild) {
            conversationList.removeChild(conversationList.firstElementChild);
        }
        window.setConversationQueueDepth(0);
    };

    window.setAgenticBusy = function (busy) {
        agenticBusy = Boolean(busy);
        sendButton.disabled = agenticBusy;
        providerSelect.disabled = agenticBusy;
        interactionInput.disabled = agenticBusy;
    };

    window.hydrateAgenticUI = function (payload) {
        payload = payload || {};
        renderState(payload.state || null);
        renderSkills(payload.skills || []);
        renderTools(payload.tools || []);
        if (payload.event) {
            renderEvent(payload.event);
        } else if (payload.state && payload.state.latest_event) {
            renderEvent(payload.state.latest_event);
        }
        if (payload.message) {
            setStatus(payload.message, payload.tone || 'idle', payload.timeoutMs || 0);
        }
    };

    window.echoes.setIdleMotionCandidates = function (candidates) {
        idleMotionCandidates = normalizeIdleMotionCandidates(candidates);
        idleMotionIndex = 0;
        renderStageDiagnostics();
    };
    window.setIdleMotionCandidates = window.echoes.setIdleMotionCandidates;

    window.setRuntimeMode = function (mode) {
        var normalized = String(mode || 'harness');
        if (runtimeModeBadge) {
            runtimeModeBadge.textContent = 'mode: ' + normalized;
        }
        setText(diagRuntimeBrainMode, normalized, 'harness');
    };

    window.setIdleVideo = function (source) {
        idleSource = source;
        setSource(source, true);
    };

    window.playTemporaryVideo = function (source) {
        setSource(source, false);
    };

    window.setRoomBackground = function (source) {
        var bg = stageBackground ? stageBackground.querySelector('img.room-background') : null;
        if (bg && source) {
            bg.src = source;
        }
    };

    window.clearRoomBackground = function () {
        var bg = stageBackground ? stageBackground.querySelector('img.room-background') : null;
        if (bg) {
            bg.removeAttribute('src');
        }
    };

    window.moveCharacter = function (x, y, scale) {
        document.documentElement.style.setProperty('--pet-anchor-shift', Number(x || 0) + 'px');
        document.documentElement.style.setProperty('--pet-floor-offset', Number(y || 0) + 'px');
        if (scale != null) {
            document.documentElement.style.setProperty('--pet-scale', String(scale));
        }
        renderStageDiagnostics();
    };

    window.setCharacterObjectPosition = function (objectPosition) {
        video.style.objectPosition = String(objectPosition || 'center bottom');
    };

    window.setActionStatus = function (message, tone, timeoutMs) {
        setStatus(message, tone, Number(timeoutMs) || 0);
    };

    window.clearActionStatus = function () {
        setStatus('', 'idle', 0);
    };

    window.setRoomCharacter = function (name) {
        roomCharacterName.textContent = name || 'Pet Preview';
    };

    window.playRoomAudio = function (source, title) {
        if (!source || typeof source !== 'string') {
            setStatus('Audio source missing.', 'warn', 3200);
            return;
        }
        audio.pause();
        audio.src = source;
        audio.load();
        audio.play().then(function () {
            setStatus(title ? 'Now playing: ' + title : 'Playing room audio.', 'music', 0);
        }).catch(function (err) {
            console.warn('[ECHOES] audio play failed:', err.message);
            setStatus('Audio playback failed: ' + err.message, 'error', 4800);
        });
    };

    window.stopRoomAudio = function () {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
    };

    window.playPanelVideo = function (source, shouldLoop, muted) {
        if (!source || !panelVideo) return;
        panelVideoGeneration += 1;
        var requestGeneration = panelVideoGeneration;
        panelVideo.muted = (muted !== false);
        panelVideo.loop = (shouldLoop === true);
        panelVideo.dataset.requestGeneration = String(requestGeneration);
        panelVideo.src = source;
        panelVideo.load();
        panelVideo.style.display = 'block';
        panelVideo.play().catch(function (err) {
            if (requestGeneration !== panelVideoGeneration) {
                return;
            }
            console.warn('[ECHOES] panel video playback failed:', err.message);
        });
    };

    window.setPanelVideoMuted = function (muted) {
        if (panelVideo) {
            panelVideo.muted = (muted !== false);
        }
    };

    window.clearPanelVideo = function () {
        if (!panelVideo) return;
        panelVideoGeneration += 1;
        panelVideo.pause();
        panelVideo.removeAttribute('src');
        panelVideo.load();
        panelVideo.style.display = 'none';
    };

    window.startMotionLoop = function (source, intervalMs) {
        window.stopMotionLoop();
        if (!source) return;
        motionLoopGeneration += 1;
        var loopGeneration = motionLoopGeneration;
        motionLoopSource = source;
        motionLoopActive = true;
        window.playTemporaryVideo(source);
        motionLoopTimer = setInterval(function () {
            if (loopGeneration !== motionLoopGeneration) {
                return;
            }
            if (motionLoopActive && motionLoopSource && (video.ended || video.paused)) {
                window.playTemporaryVideo(motionLoopSource);
            }
        }, intervalMs || 1000);
    };

    window.stopMotionLoop = function () {
        motionLoopGeneration += 1;
        if (motionLoopTimer) {
            clearInterval(motionLoopTimer);
            motionLoopTimer = null;
        }
        motionLoopSource = null;
        motionLoopActive = false;
        console.log('[ECHOES] motionLoop stopped');
    };

    window.restoreIdleMotion = function (fallbackSource) {
        if (fallbackSource) {
            idleSource = fallbackSource;
        }
        var nextSource = nextIdleCandidateSource(idleSource);
        if (!nextSource) return;
        setSource(nextSource, true);
    };

    window.resetRoomState = function () {
        window.stopRoomAudio();
        window.clearPanelVideo();
        window.stopMotionLoop();
        window.clearConversationTurns();
        window.clearActionStatus();
        if (idleSource) {
            window.restoreIdleMotion(idleSource);
        }
    };

    window.changeVideo = function (source) {
        window.setIdleVideo(source);
    };

    window.getVideoStatus = function () {
        return {
            src: video.src,
            paused: video.paused,
            currentTime: video.currentTime,
            duration: video.duration,
            readyState: video.readyState,
            statusText: actionStatusText.textContent,
            audioSrc: audio.src,
            audioPaused: audio.paused,
            characterName: roomCharacterName.textContent
        };
    };

    video.addEventListener('error', function () {
        console.warn('[ECHOES] video load failed:', video.src);
    });

    video.addEventListener('ended', function () {
        if (!video.loop) {
            window.restoreIdleMotion(idleSource);
        }
    });

    audio.addEventListener('ended', function () {
        console.log('[ECHOES:ROOM_AUDIO_ENDED]');
        setStatus('Music playback finished.', 'idle', 2200);
    });

    audio.addEventListener('error', function () {
        console.warn('[ECHOES] audio load failed:', audio.src);
        setStatus('Audio playback failed.', 'error', 4200);
    });

    if (panelVideo) {
        panelVideo.addEventListener('ended', function () {
            console.log('[ECHOES:PANEL_ENDED]');
        });
    }

    try {
        document.body.dataset.agenticPanel = 'expanded';
        updateStageScale();
        syncAgenticPanelOffset();
        if (typeof ResizeObserver !== 'undefined') {
            resizeObserver = new ResizeObserver(function () {
                updateStageScale();
                syncAgenticPanelOffset();
            });
            resizeObserver.observe(document.documentElement);
        } else {
            window.addEventListener('resize', updateStageScale);
            window.addEventListener('resize', syncAgenticPanelOffset);
        }
        setupForms();
        wireScenarioButtons();
        wireDynamicActions();
        setupWebChannel();
        setStatus('', 'idle', 0);
        window.setConversationQueueDepth(0);
        window.setRuntimeMode('harness');
    } catch (error) {
        console.error('[ECHOES APP.JS] init error:', error.message);
    }

    console.log('[ECHOES APP.JS] init complete — harnessBridge:', typeof harnessBridge);
})();
