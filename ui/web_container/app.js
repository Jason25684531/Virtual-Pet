(function () {
    'use strict';

    console.log('[ECHOES APP.JS] IIFE started — DOM ready, binding handlers');

    // ── DOM 參照（僅保留有效元素）────────────────────────────
    var stageRoot = document.getElementById('stage-root');
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
    var runtimeSttStatus = document.getElementById('runtime-stt-status');
    var runtimeSttButton = document.getElementById('runtime-stt-button');
    var runtimeResetButton = document.getElementById('runtime-reset-button');
    var runtimeJokeButton = document.getElementById('runtime-joke-button');
    var runtimeShareButton = document.getElementById('runtime-share-button');
    var runtimeMusicButton = document.getElementById('runtime-music-button');
    var runtimeNewsButton = document.getElementById('runtime-news-button');
    var skillList = document.getElementById('skill-list');
    var skillCountBadge = document.getElementById('skill-count-badge');
    var refreshSkillsButton = document.getElementById('refresh-skills-button');
    var panelToggleButton = document.getElementById('agentic-panel-toggle');

    // ── 狀態變數 ──────────────────────────────────────────────
    var idleSource = '';
    var idleMotionCandidates = [];
    var idleMotionIndex = 0;
    var statusTimer = null;
    var defaultStatusText = 'Waiting for room updates.';
    var harnessBridge = null;
    var agenticBusy = false;
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

    // ── 通用工具 ──────────────────────────────────────────────

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

    function normalizeProjectAssetSource(source) {
        var trimmed = String(source || '').trim();
        if (!trimmed) return '';
        if (/^(?:[a-z][a-z0-9+.-]*:|\/|\.\/|\.\.\/)/i.test(trimmed)) return trimmed;
        return '../../' + trimmed.replace(/^\/+/, '');
    }

    function setStatus(message, tone, timeoutMs) {
        if (statusTimer) {
            clearTimeout(statusTimer);
            statusTimer = null;
        }
        if (!actionStatus) return;
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
    }

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
    }

    function toggleAgenticPanel() {
        var collapsed = document.body.dataset.agenticPanel === 'collapsed';
        document.body.dataset.agenticPanel = collapsed ? 'expanded' : 'collapsed';
        syncAgenticPanelOffset();
    }

    // ── Idle Motion ───────────────────────────────────────────

    function normalizeIdleMotionCandidates(candidates) {
        if (!Array.isArray(candidates)) return [];
        return candidates.map(function (candidate) {
            if (typeof candidate === 'string') return { source: candidate, weight: 1 };
            if (!candidate || !candidate.source) return null;
            return { source: String(candidate.source), weight: Math.max(1, Number(candidate.weight || 1)) };
        }).filter(Boolean);
    }

    function nextIdleCandidateSource(fallbackSource) {
        if (!idleMotionCandidates.length) return fallbackSource || idleSource;
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

    // ── Conversation Turns ────────────────────────────────────

    function trimConversationTurns() {
        while (conversationList && conversationList.children.length > maxConversationTurns) {
            var firstChild = conversationList.firstElementChild;
            if (!firstChild) break;
            conversationTurns.delete(firstChild.dataset.turnId);
            conversationList.removeChild(firstChild);
        }
    }

    function ensureConversationTurn(turnId, sourceLabel) {
        var existing = conversationTurns.get(turnId);
        if (existing) return existing;

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
        if (conversationList) conversationList.appendChild(article);

        var turn = { root: article, userText: userText, assistantText: assistantText };
        conversationTurns.set(turnId, turn);
        trimConversationTurns();
        return turn;
    }

    // ── 渲染函式 ──────────────────────────────────────────────

    function renderSkills(skills) {
        var items = Array.isArray(skills) ? skills : [];
        if (skillCountBadge) skillCountBadge.textContent = items.length + ' skills';
        if (!skillList) return;
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
        console.log('[ECHOES UI] background=' + status);
    }

    function renderState(state) {
        if (!state) return;
        latestRuntimeState = state;
        renderXpState(state.xp || null);
        renderBackgroundStatus(state.background || null);
    }

    function renderRuntimeControls(runtimeControls) {
        var controls = runtimeControls || {};
        var stt = controls.stt || {};
        if (runtimeSttStatus) {
            runtimeSttStatus.textContent = stt.statusLabel || stt.state || 'idle';
        }
        if (runtimeSttButton) {
            runtimeSttButton.textContent = stt.label || '開始收音';
            runtimeSttButton.disabled = stt.enabled === false;
            runtimeSttButton.dataset.state = stt.state || 'idle';
        }
        if (runtimeResetButton) {
            runtimeResetButton.textContent = 'Reset';
            runtimeResetButton.disabled = controls.reset && controls.reset.enabled === false;
        }
    }

    // ── Bridge 呼叫 ───────────────────────────────────────────

    function callBridge(method) {
        var args = Array.prototype.slice.call(arguments, 1);
        if (!harnessBridge || typeof harnessBridge[method] !== 'function') {
            var msg = 'Bridge not ready — cannot call: ' + method;
            console.warn('[ECHOES UI] ' + msg);
            setStatus('Bridge unavailable. Check qwebchannel.js.', 'error', 0);
            return;
        }
        return harnessBridge[method].apply(harnessBridge, args);
    }

    // ── 事件綁定 ──────────────────────────────────────────────

    function setupForms() {
        if (panelToggleButton) {
            panelToggleButton.addEventListener('click', toggleAgenticPanel);
        }
        if (runtimeSttButton) {
            runtimeSttButton.addEventListener('click', function () {
                callBridge('toggleStt');
            });
        }
        if (runtimeResetButton) {
            runtimeResetButton.addEventListener('click', function () {
                callBridge('resetRuntime');
            });
        }
        if (runtimeJokeButton) {
            runtimeJokeButton.addEventListener('click', function () {
                callBridge('triggerQuickIntent', 'joke');
            });
        }
        if (runtimeShareButton) {
            runtimeShareButton.addEventListener('click', function () {
                callBridge('triggerQuickIntent', 'share');
            });
        }
        if (runtimeMusicButton) {
            runtimeMusicButton.addEventListener('click', function () {
                callBridge('triggerOverlayAction', 'play_music');
            });
        }
        if (runtimeNewsButton) {
            runtimeNewsButton.addEventListener('click', function () {
                callBridge('triggerOverlayAction', 'report_news');
            });
        }
        if (refreshSkillsButton) {
            refreshSkillsButton.addEventListener('click', function () {
                console.log('[ECHOES UI] refresh skills clicked');
                callBridge('refreshState');
            });
        }
    }

    function wireDynamicActions() {
        if (!skillList) return;
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
    }

    function setupWebChannel() {
        if (typeof window.QWebChannel === 'undefined') {
            var msg = 'QWebChannel not available (qwebchannel.js failed to load)';
            console.error('[ECHOES UI]', msg);
            setStatus('Bridge unavailable. Check qwebchannel.js.', 'error', 0);
            return;
        }
        if (!window.qt || !window.qt.webChannelTransport) {
            var msg2 = 'qt.webChannelTransport not available (not inside Qt WebEngine?)';
            console.error('[ECHOES UI]', msg2);
            setStatus('Qt transport missing. Run via PyQt5.', 'error', 0);
            return;
        }
        new window.QWebChannel(window.qt.webChannelTransport, function (channel) {
            harnessBridge = channel.objects.harnessBridge || null;
            if (!harnessBridge) {
                console.error('[ECHOES UI] harnessBridge object not found in channel.objects');
                setStatus('Bridge object missing. Check PyQt registration.', 'error', 0);
                return;
            }
            console.log('[ECHOES UI] bridge=ready');
            setStatus('Bridge ready.', 'idle', 1800);
            callBridge('refreshState');
        });
    }

    // ── Bridge 函式（window.* — Python → JS，完整保留）────────

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
    };

    window.hydrateAgenticUI = function (payload) {
        payload = payload || {};
        renderState(payload.state || null);
        renderRuntimeControls(payload.runtimeControls || null);
        renderSkills(payload.skills || []);
        if (payload.message) {
            setStatus(payload.message, payload.tone || 'idle', payload.timeoutMs || 0);
        }
    };

    window.updateRuntimeControls = function (runtimeControls) {
        renderRuntimeControls(runtimeControls || null);
    };

    window.echoes.setIdleMotionCandidates = function (candidates) {
        idleMotionCandidates = normalizeIdleMotionCandidates(candidates);
        idleMotionIndex = 0;
    };
    window.setIdleMotionCandidates = window.echoes.setIdleMotionCandidates;

    window.setRuntimeMode = function (mode) {
        var normalized = String(mode || 'harness');
        if (runtimeModeBadge) {
            runtimeModeBadge.textContent = 'mode: ' + normalized;
        }
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
        if (bg && source) bg.src = normalizeProjectAssetSource(source);
    };

    window.clearRoomBackground = function () {
        var bg = stageBackground ? stageBackground.querySelector('img.room-background') : null;
        if (bg) bg.removeAttribute('src');
    };

    window.moveCharacter = function (x, y, scale, cropZoom) {
        document.documentElement.style.setProperty('--pet-anchor-shift', Number(x || 0) + 'px');
        document.documentElement.style.setProperty('--pet-floor-offset', Number(y || 0) + 'px');
        if (scale != null) {
            document.documentElement.style.setProperty('--pet-scale', String(scale));
        }
        if (cropZoom != null) {
            var clamped = Math.min(8, Math.max(1, Number(cropZoom)));
            document.documentElement.style.setProperty('--video-crop-zoom', String(clamped));
        }
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
        if (roomCharacterName) roomCharacterName.textContent = name || 'Pet Preview';
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
            if (requestGeneration !== panelVideoGeneration) return;
            console.warn('[ECHOES] panel video playback failed:', err.message);
        });
    };

    window.setPanelVideoMuted = function (muted) {
        if (panelVideo) panelVideo.muted = (muted !== false);
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
            if (loopGeneration !== motionLoopGeneration) return;
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
        if (fallbackSource) idleSource = fallbackSource;
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
        if (idleSource) window.restoreIdleMotion(idleSource);
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
            statusText: actionStatusText ? actionStatusText.textContent : '',
            audioSrc: audio.src,
            audioPaused: audio.paused,
            characterName: roomCharacterName ? roomCharacterName.textContent : ''
        };
    };

    // ── 媒體事件 ──────────────────────────────────────────────

    video.addEventListener('error', function () {
        console.warn('[ECHOES] video load failed:', video.src);
    });

    video.addEventListener('ended', function () {
        if (!video.loop) window.restoreIdleMotion(idleSource);
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

    // ── 初始化 ────────────────────────────────────────────────

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
