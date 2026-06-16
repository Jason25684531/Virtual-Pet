(function () {
    'use strict';

    console.log('[ECHOES APP.JS] IIFE started — DOM ready, binding handlers');

    // -----------------------------------------------------------------------
    // DOM 參考
    // -----------------------------------------------------------------------
    var video              = document.getElementById('pet-video');
    var character          = document.getElementById('pet-character');
    var audio              = document.getElementById('room-audio');
    var roomCharacterName  = document.getElementById('room-character-name');
    var actionStatus       = document.getElementById('action-status');
    var actionStatusText   = document.getElementById('action-status-text');
    var xpDisplay          = document.getElementById('xp-display');
    var xpProgressBar      = document.getElementById('xp-progress-bar');
    var xpThresholdDisplay = document.getElementById('xp-threshold-display');
    var providerSummary    = document.getElementById('provider-summary');
    var resultReply        = document.getElementById('result-reply');
    var resultSkill        = document.getElementById('result-skill');
    var resultTool         = document.getElementById('result-tool');
    var resultXpDelta      = document.getElementById('result-xp-delta');
    var resultReward       = document.getElementById('result-reward');
    var resultAsset        = document.getElementById('result-asset');
    var resultBehavior     = document.getElementById('result-behavior');
    var resultWebmKey      = document.getElementById('result-webm-key');
    var resultSaved        = document.getElementById('result-saved');
    var warningsList       = document.getElementById('warnings-list');
    var skillList          = document.getElementById('skill-list');
    var toolList           = document.getElementById('tool-list');
    var skillCountBadge    = document.getElementById('skill-count-badge');
    var toolCountBadge     = document.getElementById('tool-count-badge');
    var interactionInput   = document.getElementById('interaction-input');
    var providerSelect     = document.getElementById('provider-select');
    var sendButton         = document.getElementById('send-button');
    var refreshStateButton = document.getElementById('refresh-state-button');
    var refreshSkillsButton= document.getElementById('refresh-skills-button');
    var refreshToolsButton = document.getElementById('refresh-tools-button');
    var skillForm          = document.getElementById('skill-form');
    var toolForm           = document.getElementById('tool-form');
    var bridgeStatusEl     = document.getElementById('bridge-status');
    var lastActionEl       = document.getElementById('last-action');
    var lastErrorEl        = document.getElementById('last-error');
    var backgroundStatusEl = document.getElementById('background-status');
    var micButton          = document.getElementById('mic-button');
    var speakReplyButton   = document.getElementById('speak-reply-button');
    var voiceStatus        = document.getElementById('voice-status');
    var voiceSttStatus     = document.getElementById('voice-stt-status');
    var voiceTtsStatus     = document.getElementById('voice-tts-status');
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

    var idleSource = '';
    var statusTimer = null;
    var defaultStatusText = 'Waiting for room updates.';
    var harnessBridge = null;
    var agenticBusy = false;
    var latestVoiceState = null;
    var latestReplyText = '';

    // -----------------------------------------------------------------------
    // 診斷 UI 輔助
    // -----------------------------------------------------------------------
    function setDiagBridgeStatus(text, isReady) {
        if (!bridgeStatusEl) return;
        bridgeStatusEl.textContent = 'Bridge: ' + text;
        bridgeStatusEl.dataset.ready = isReady ? 'true' : 'false';
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

    // -----------------------------------------------------------------------
    // 視頻控制
    // -----------------------------------------------------------------------
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

    // -----------------------------------------------------------------------
    // 狀態列
    // -----------------------------------------------------------------------
    function setStatus(message, tone, timeoutMs) {
        if (statusTimer) {
            clearTimeout(statusTimer);
            statusTimer = null;
        }
        actionStatus.dataset.tone = tone || 'idle';
        actionStatusText.textContent = message || defaultStatusText;
        if (timeoutMs && timeoutMs > 0) {
            statusTimer = window.setTimeout(function () {
                window.clearActionStatus();
            }, timeoutMs);
        }
    }

    // -----------------------------------------------------------------------
    // HTML escape
    // -----------------------------------------------------------------------
    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function setText(element, value, fallback) {
        if (!element) return;
        element.textContent = value == null || value === '' ? (fallback || '-') : String(value);
    }

    // -----------------------------------------------------------------------
    // 渲染函數
    // -----------------------------------------------------------------------
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
                '  <p class="entity-card__meta">required_tool: ' + escapeHtml(item.required_tool || '-') + '</p>',
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
                '      <p class="entity-card__meta">' + escapeHtml(item.status) + '</p>',
                '    </div>',
                '    <span class="status-pill">' + (item.enabled ? 'enabled' : 'disabled') + '</span>',
                '  </div>',
                '  <p class="entity-card__meta">' + escapeHtml(item.description || '-') + '</p>',
                '  <p class="entity-card__meta">risk: ' + escapeHtml(item.risk_level || '-') + ' | permission: ' + escapeHtml(item.permission_requirement || '-') + '</p>',
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
        var tool    = payload.tool || {};
        var asset   = payload.asset_summary || {};
        latestReplyText = payload.reply || latestReplyText || '';
        resultReply.textContent     = payload.reply || 'Waiting for input.';
        resultSkill.textContent     = payload.matched_skill || '-';
        resultTool.textContent      = tool.name
            ? [tool.name, tool.status || '-', tool.reason || ''].filter(Boolean).join(' | ')
            : '-';
        resultXpDelta.textContent   = String(payload.xp_delta == null ? 0 : payload.xp_delta);
        resultReward.textContent    = Array.isArray(payload.reward_summary) && payload.reward_summary.length
            ? payload.reward_summary.join(', ')
            : '-';
        resultAsset.textContent     = asset.asset_id || asset.webm_key || asset.status || '-';
        resultBehavior.textContent  = payload.behavior_id || 'idle';
        resultWebmKey.textContent   = payload.webm_key || 'idle';
        resultSaved.textContent     = String(Boolean(payload.saved_to_db));
        if (payload.provider_status && payload.provider_status.provider_type) {
            providerSummary.textContent = 'provider: ' + payload.provider_status.provider_type;
        }
        renderWarnings(payload.warnings || []);
    }

    function renderXpState(xp) {
        if (!xp) return;
        if (xp.display) {
            xpDisplay.textContent = xp.display;
        }
        if (xpProgressBar) {
            var percent = Number(xp.progress_percent || 0);
            xpProgressBar.style.width = Math.max(0, Math.min(100, percent)) + '%';
        }
        if (xpThresholdDisplay) {
            xpThresholdDisplay.textContent = String(xp.xp_total || 0) + ' / ' + String(xp.next_level_xp || 100) + ' XP';
        }
    }

    function renderBackgroundStatus(background) {
        var status = background && background.status ? background.status : 'missing';
        setText(backgroundStatusEl, 'Background: ' + status);
        console.log('[ECHOES UI] background=' + status);
    }

    function voiceStatusLabel(status) {
        if (status === 'configured_not_implemented') return 'configured but not implemented';
        return status || 'missing';
    }

    function renderVoiceStatus(voice) {
        latestVoiceState = voice || null;
        var stt = voice && voice.stt ? voice.stt : {};
        var tts = voice && voice.tts ? voice.tts : {};
        setText(voiceStatus, 'voice: ' + voiceStatusLabel(stt.status) + ' / ' + voiceStatusLabel(tts.status));
        setText(voiceSttStatus, stt.message || voiceStatusLabel(stt.status));
        setText(voiceTtsStatus, tts.message || voiceStatusLabel(tts.status));
        console.log('[ECHOES UI] voice.tts=configured status=' + (tts.status || 'missing'));
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
    }

    function renderState(state) {
        if (!state) return;
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

    // -----------------------------------------------------------------------
    // Bridge 呼叫 — 帶 log 與錯誤處理
    // -----------------------------------------------------------------------
    function callBridge(method) {
        if (!harnessBridge) {
            var msg = 'Bridge not ready — cannot call: ' + method;
            console.warn('[ECHOES UI] ' + msg);
            setDiagLastError(msg);
            setStatus('Bridge not ready. Try restarting the UI.', 'error', 5000);
            return;
        }
        if (typeof harnessBridge[method] !== 'function') {
            var msg2 = 'Bridge method missing: ' + method;
            console.warn('[ECHOES UI] ' + msg2);
            setDiagLastError(msg2);
            return;
        }
        try {
            setDiagLastError('');
            var args = Array.prototype.slice.call(arguments, 1);
            harnessBridge[method].apply(harnessBridge, args);
        } catch (err) {
            var errMsg = method + ' threw: ' + err.message;
            console.error('[ECHOES UI]', errMsg, err);
            setDiagLastError(errMsg);
            setStatus('Bridge error: ' + err.message, 'error', 5000);
        }
    }

    // -----------------------------------------------------------------------
    // Scenario 按鈕
    // -----------------------------------------------------------------------
    function wireScenarioButtons() {
        Array.prototype.forEach.call(document.querySelectorAll('.scenario-button'), function (button) {
            button.addEventListener('click', function () {
                var scenarioText = button.dataset.text || '';
                console.log('[ECHOES UI] scenario clicked:', scenarioText);
                setDiagLastAction('scenario: ' + scenarioText);
                interactionInput.value = scenarioText;
                triggerSend();
            });
        });
    }

    // -----------------------------------------------------------------------
    // 動態 skill / tool 事件代理
    // -----------------------------------------------------------------------
    function wireDynamicActions() {
        skillList.addEventListener('click', function (event) {
            var toggle = event.target.closest('[data-skill-toggle]');
            var remove = event.target.closest('[data-skill-delete]');
            if (toggle) {
                var skillId = toggle.dataset.skillToggle;
                var enabled = toggle.dataset.enabled === 'true';
                console.log('[ECHOES UI] skill toggle clicked:', skillId, '->', enabled);
                setDiagLastAction('skill toggle: ' + skillId);
                callBridge('toggleSkill', skillId, enabled);
            } else if (remove) {
                var skillIdDel = remove.dataset.skillDelete;
                console.log('[ECHOES UI] skill delete clicked:', skillIdDel);
                setDiagLastAction('skill delete: ' + skillIdDel);
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
                setDiagLastAction('tool toggle: ' + toolName);
                callBridge('toggleTool', toolName, enabled);
            } else if (remove) {
                var toolNameDel = remove.dataset.toolDelete;
                console.log('[ECHOES UI] tool delete clicked:', toolNameDel);
                setDiagLastAction('tool delete: ' + toolNameDel);
                // 修正：Python 方法名為 deleteToolConfig
                callBridge('deleteToolConfig', toolNameDel);
            }
        });
    }

    // -----------------------------------------------------------------------
    // Send 觸發
    // -----------------------------------------------------------------------
    function triggerSend() {
        var text = interactionInput.value.trim();
        if (!text) {
            setStatus('Please enter some text first.', 'warn', 2200);
            return;
        }
        if (agenticBusy) {
            setStatus('Interaction already running.', 'warn', 2200);
            return;
        }
        console.log('[ECHOES UI] action=send provider=' + providerSelect.value);
        console.log('[ECHOES UI] send clicked, text:', text, 'provider:', providerSelect.value);
        setDiagLastAction('send: ' + text.substring(0, 40));
        callBridge('sendText', text, providerSelect.value);
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

    // -----------------------------------------------------------------------
    // 表單 / 靜態按鈕事件
    // -----------------------------------------------------------------------
    function setupForms() {
        sendButton.addEventListener('click', function () {
            console.log('[ECHOES UI] send clicked');
            triggerSend();
        });

        refreshStateButton.addEventListener('click', function () {
            console.log('[ECHOES UI] refresh state clicked');
            setDiagLastAction('refresh state');
            callBridge('refreshState');
        });
        refreshSkillsButton.addEventListener('click', function () {
            console.log('[ECHOES UI] refresh skills clicked');
            setDiagLastAction('refresh skills');
            callBridge('refreshState');
        });
        refreshToolsButton.addEventListener('click', function () {
            console.log('[ECHOES UI] refresh tools clicked');
            setDiagLastAction('refresh tools');
            callBridge('refreshState');
        });

        interactionInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                triggerSend();
            }
        });

        skillForm.addEventListener('submit', function (event) {
            event.preventDefault();
            var payload = {
                skill_id:         document.getElementById('skill-id').value.trim(),
                display_name:     document.getElementById('skill-display-name').value.trim(),
                description:      document.getElementById('skill-description').value.trim(),
                triggers:         document.getElementById('skill-triggers').value.trim(),
                default_behavior: document.getElementById('skill-behavior').value.trim(),
                required_tool:    document.getElementById('skill-required-tool').value.trim()
            };
            console.log('[ECHOES UI] add skill submitted:', payload.skill_id);
            setDiagLastAction('add skill: ' + payload.skill_id);
            callBridge('addSkill', JSON.stringify(payload));
            skillForm.reset();
        });

        toolForm.addEventListener('submit', function (event) {
            event.preventDefault();
            var payload = {
                tool_name:   document.getElementById('tool-name').value.trim(),
                description: document.getElementById('tool-description').value.trim(),
                risk_level:  document.getElementById('tool-risk-level').value,
                enabled:     document.getElementById('tool-enabled').checked
            };
            console.log('[ECHOES UI] add tool config submitted:', payload.tool_name);
            setDiagLastAction('add tool: ' + payload.tool_name);
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

    // -----------------------------------------------------------------------
    // WebChannel 初始化 — 核心修復
    // -----------------------------------------------------------------------
    function setupWebChannel() {
        // 診斷顯示初始狀態
        setDiagBridgeStatus('initializing…', false);

        // 檢查 Qt WebChannel 是否可用
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

        // 建立 QWebChannel，物件名稱必須與 Python 端 registerObject 一致：
        // self._channel.registerObject("harnessBridge", self._bridge)
        new window.QWebChannel(window.qt.webChannelTransport, function (channel) {
            harnessBridge = channel.objects.harnessBridge || null;
            if (!harnessBridge) {
                var errMsg = 'harnessBridge object not found in channel.objects';
                console.error('[ECHOES UI]', errMsg, 'available:', Object.keys(channel.objects));
                setDiagBridgeStatus('not ready — object missing', false);
                setStatus('Bridge object missing. Check PyQt registration.', 'error', 0);
                return;
            }

            console.log('[ECHOES UI] bridge=ready');
            console.log('[ECHOES UI] QWebChannel ready, harnessBridge connected');
            setDiagBridgeStatus('ready', true);
            setDiagLastError('');
            setStatus('Bridge ready.', 'idle', 1800);

            // 初始化完成後立即刷新 UI
            callBridge('refreshState');
        });
    }

    // -----------------------------------------------------------------------
    // 從 Python 端呼叫的全域函數
    // -----------------------------------------------------------------------
    window.setAgenticBusy = function (busy) {
        agenticBusy = Boolean(busy);
        sendButton.disabled         = agenticBusy;
        providerSelect.disabled     = agenticBusy;
        interactionInput.disabled   = agenticBusy;
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

    // -----------------------------------------------------------------------
    // 視頻 / 音頻事件
    // -----------------------------------------------------------------------
    video.addEventListener('error', function () {
        console.warn('[ECHOES] video load failed:', video.src);
    });

    video.addEventListener('ended', function () {
        if (!video.loop && idleSource) {
            setSource(idleSource, true);
        }
    });

    audio.addEventListener('ended', function () {
        setStatus('Music playback finished.', 'idle', 2200);
    });

    audio.addEventListener('error', function () {
        console.warn('[ECHOES] audio load failed:', audio.src);
        setStatus('Audio playback failed.', 'error', 4200);
    });

    // -----------------------------------------------------------------------
    // 從 Python 端呼叫的視頻 / UI 函數
    // -----------------------------------------------------------------------
    window.setIdleVideo = function (source) {
        idleSource = source;
        setSource(source, true);
    };

    window.playTemporaryVideo = function (source) {
        setSource(source, false);
    };

    window.moveCharacter = function (x, y) {
        var target = character || video;
        if (!target) {
            console.warn('[ECHOES] character target missing');
            return;
        }
        target.style.transform = 'translate3d(' + Number(x) + 'px, ' + Number(y) + 'px, 0)';
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

    // -----------------------------------------------------------------------
    // 初始化 — try-catch 確保單一錯誤不會導致所有 event listener 全部失敗
    // -----------------------------------------------------------------------
    try { setupForms(); } catch(e) { console.error('[ECHOES APP.JS] setupForms error:', e.message); }
    try { wireScenarioButtons(); } catch(e) { console.error('[ECHOES APP.JS] wireScenarioButtons error:', e.message); }
    try { wireDynamicActions(); } catch(e) { console.error('[ECHOES APP.JS] wireDynamicActions error:', e.message); }
    try { setupWebChannel(); } catch(e) { console.error('[ECHOES APP.JS] setupWebChannel error:', e.message); }
    setStatus('', 'idle', 0);

    console.log('[ECHOES APP.JS] init complete — harnessBridge:', typeof harnessBridge);

})();
