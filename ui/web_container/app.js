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
    var characterSkillList = document.getElementById('character-skill-list');
    var characterSkillCount = document.getElementById('character-skill-count');
    var personaValidationStatus = document.getElementById('persona-validation-status');
    var personaTextarea = document.getElementById('persona-textarea');
    var personaSaveButton = document.getElementById('persona-save-button');
    var personaCancelButton = document.getElementById('persona-cancel-button');
    var personaBuiltinSkillList = document.getElementById('persona-builtin-skill-list');
    var personaLocalSkillList = document.getElementById('persona-local-skill-list');
    var personaLocalSkillNewButton = document.getElementById('persona-local-skill-new-button');
    var personaLocalSkillForm = document.getElementById('persona-local-skill-form');
    var localSkillIdInput = document.getElementById('local-skill-id-input');
    var localSkillDisplayNameInput = document.getElementById('local-skill-display-name-input');
    var localSkillDescriptionInput = document.getElementById('local-skill-description-input');
    var localSkillTriggersInput = document.getElementById('local-skill-triggers-input');
    var localSkillBehaviorInput = document.getElementById('local-skill-behavior-input');
    var localSkillRequiredToolInput = document.getElementById('local-skill-required-tool-input');
    var localSkillFormError = document.getElementById('local-skill-form-error');
    var localSkillSaveButton = document.getElementById('local-skill-save-button');
    var localSkillFormCancelButton = document.getElementById('local-skill-form-cancel-button');
    var personaPreviewInput = document.getElementById('persona-preview-input');
    var personaPreviewButton = document.getElementById('persona-preview-button');
    var personaPreviewResult = document.getElementById('persona-preview-result');

    // ── UC01-1 / UC02-1 / UC03-1 / UC05-1 陪伴 dock DOM 參照 ────────
    var hudScore = document.getElementById('hud-score');
    var hudScoreText = document.getElementById('hud-score-text');
    var hudScoreLevel = document.getElementById('hud-score-level');
    var hudScoreDelta = document.getElementById('hud-score-delta');
    var hudScoreProgressFill = document.getElementById('hud-score-progress-fill');
    var appScreens = document.getElementById('app-screens');
    var screenMainMenu = document.getElementById('screen-main-menu');
    var screenPresetSelect = document.getElementById('screen-preset-select');
    var screenLoadSave = document.getElementById('screen-load-save');
    var menuCreateButton = document.getElementById('menu-create-button');
    var menuLoadButton = document.getElementById('menu-load-button');
    var menuSettingsButton = document.getElementById('menu-settings-button');
    var menuQuitButton = document.getElementById('menu-quit-button');
    var presetCarouselIndex = document.getElementById('preset-carousel-index');
    var presetCarouselPrev = document.getElementById('preset-carousel-prev');
    var presetCarouselNext = document.getElementById('preset-carousel-next');
    var presetName = document.getElementById('preset-name');
    var presetPersona = document.getElementById('preset-persona');
    var presetSelectButton = document.getElementById('preset-select-button');
    var presetCustomizeToggle = document.getElementById('preset-customize-toggle');
    var presetCustomizePanel = document.getElementById('preset-customize-panel');
    var presetCustomizeGenerate = document.getElementById('preset-customize-generate');
    var presetThumbList = document.getElementById('preset-thumb-list');
    var presetBackButton = document.getElementById('preset-back-button');
    var saveCardGrid = document.getElementById('save-card-grid');
    var saveBackButton = document.getElementById('save-back-button');
    var saveDeleteButton = document.getElementById('save-delete-button');
    var saveContinueButton = document.getElementById('save-continue-button');
    var agentCommandInput = document.getElementById('agent-command-input');
    var agentCommandMic = document.getElementById('agent-command-mic');
    var agentCommandSubmit = document.getElementById('agent-command-submit');
    var agentChipJoke = document.getElementById('agent-chip-joke');
    var agentChipMusic = document.getElementById('agent-chip-music');
    var agentChipNews = document.getElementById('agent-chip-news');
    var agentResultText = document.getElementById('agent-result-text');
    var agentResultDelta = document.getElementById('agent-result-delta');
    var companionDockPanel = document.getElementById('companion-dock-panel');
    var dockButtons = Array.prototype.slice.call(document.querySelectorAll('.dock-icon'));
    var dockPanels = Array.prototype.slice.call(document.querySelectorAll('.dock-panel'));
    var talkTextInput = document.getElementById('talk-text-input');
    var talkSendButton = document.getElementById('talk-send-button');
    var companionSettingsButton = document.getElementById('companion-settings-button');
    var companionLeaveButton = document.getElementById('companion-leave-button');

    // ── 狀態變數 ──────────────────────────────────────────────
    var idleSource = '';
    var idleMotionCandidates = [];
    var idleMotionIndex = 0;
    var statusTimer = null;
    var defaultStatusText = 'Waiting for room updates.';
    var harnessBridge = null;
    var characterBridge = null;
    var agenticBusy = false;
    var presetList = [];
    var presetIndex = 0;
    var saveList = [];
    var selectedSaveId = null;
    var hudPollTimer = null;
    var motionLoopTimer = null;
    var motionLoopSource = null;
    var motionLoopActive = false;
    var motionLoopGeneration = 0;
    var panelVideoGeneration = 0;
    var conversationTurns = new Map();
    var maxConversationTurns = 3;
    var latestRuntimeState = null;
    var latestHudState = null;
    var currentCharacterSkills = [];
    var hudDeltaTimer = null;
    var resizeObserver = null;
    var personaCharacterId = null;
    var personaOriginal = null;
    var editingLocalSkillId = null;

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

    function formatPlaytime(totalSeconds) {
        var seconds = Math.max(0, Number(totalSeconds) || 0);
        var totalMinutes = Math.floor(seconds / 60);
        var hours = Math.floor(totalMinutes / 60);
        var minutes = totalMinutes % 60;
        if (hours > 0) return hours + 'h' + minutes + 'm';
        return Math.max(0, totalMinutes) + 'm';
    }

    function formatLastPlayed(isoText) {
        if (!isoText) return '尚未遊玩';
        var parsed = new Date(String(isoText));
        if (Number.isNaN(parsed.getTime())) return '尚未遊玩';
        var month = String(parsed.getMonth() + 1).padStart(2, '0');
        var day = String(parsed.getDate()).padStart(2, '0');
        return month + '/' + day;
    }

    function setAgentResult(message, delta) {
        // 回覆一律由 Conversation 區承接；不得為了結果自動打開 Skills。
        void message;
        void delta;
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
        // 1920x1080 鎖定，不需要動態縮放
        document.documentElement.style.setProperty('--stage-scale', '1');
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

    function skillCardTitleMeta(item) {
        return '<p class="entity-card__title">' + escapeHtml(item.display_name || item.skill_id) + '</p>' +
            '<p class="entity-card__meta">' + escapeHtml(item.skill_id) + '</p>';
    }

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
                '    <div>' + skillCardTitleMeta(item) + '</div>',
                '    <span class="status-pill">' + (item.enabled ? 'enabled' : 'disabled') + '</span>',
                '  </div>',
                '  <p class="entity-card__meta">' + escapeHtml(item.description || '-') + '</p>',
                '  <p class="entity-card__meta">triggers: ' + escapeHtml((item.triggers || []).join(', ') || '-') + '</p>',
                '  <p class="entity-card__meta">tool: ' + escapeHtml(item.required_tool || '-') + ' · priority: ' + escapeHtml(String(item.priority || 0)) + (item.capability && item.capability !== 'general' ? ' · ' + escapeHtml(item.capability) : '') + '</p>',
                '  <p class="entity-card__meta">path: ' + escapeHtml(item.file_path || 'built-in') + '</p>',
                '  <div class="entity-card__actions">',
                '    <button class="secondary-button" type="button" data-skill-toggle="' + escapeHtml(item.skill_id) + '" data-enabled="' + String(!item.enabled) + '">' + toggleLabel + '</button>',
                '    <button class="danger-button" type="button" data-skill-delete="' + escapeHtml(item.skill_id) + '">' + deleteLabel + '</button>',
                '  </div>',
                '</article>'
            ].join('');
        }).join('');
    }

    function renderCharacterSkills(skills) {
        if (!Array.isArray(skills)) return;
        var items = skills;
        currentCharacterSkills = items;
        if (characterSkillCount) characterSkillCount.textContent = items.length + ' skills';
        if (!characterSkillList) return;
        if (!items.length) {
            characterSkillList.innerHTML = '<div class="entity-card"><p class="entity-card__title">目前角色尚無已配置的技能。</p></div>';
            return;
        }
        characterSkillList.innerHTML = items.map(function (item) {
            var isEnabled = item.enabled !== false;
            return [
                '<article class="entity-card">',
                '  <div class="entity-card__head">',
                '    <div>' + skillCardTitleMeta(item) + '</div>',
                '    <span class="status-pill">' + (isEnabled ? '啟用中' : '已關閉') + '</span>',
                '  </div>',
                '  <p class="entity-card__meta">' + escapeHtml(item.description || '-') + '</p>',
                '  <div class="entity-card__actions">',
                '    <button class="secondary-button" type="button" data-character-skill-toggle="' + escapeHtml(item.skill_id) + '" data-enabled="' + String(!isEnabled) + '">' + (isEnabled ? '關閉' : '啟動') + '</button>',
                isEnabled ? '    <button class="primary-button" type="button" data-skill-trigger="' + escapeHtml(item.skill_id) + '">立即執行</button>' : '',
                '  </div>',
                '</article>'
            ].join('');
        }).join('');
    }

    // ── Character Persona / Skill Customization（人設與技能客製化）───

    function setInputValue(el, value) {
        if (el) el.value = value == null ? '' : value;
    }

    function discardPersonaDraft() {
        personaCharacterId = null;
        personaOriginal = null;
        if (personaTextarea) personaTextarea.value = '';
        if (personaBuiltinSkillList) personaBuiltinSkillList.innerHTML = '';
        if (personaLocalSkillList) personaLocalSkillList.innerHTML = '';
        hideLocalSkillForm();
        hidePreviewResult();
    }

    function renderPersonaEditor(customization) {
        personaOriginal = customization;
        personaCharacterId = customization.character_id;
        if (personaTextarea) personaTextarea.value = customization.persona || '';
        if (personaValidationStatus) personaValidationStatus.textContent = 'schema v' + (customization.schema_version || 1);
        renderBuiltinSkillOverrides(customization.builtin_skills || []);
        renderLocalSkillsForCustomization(customization.local_skills || []);
        hideLocalSkillForm();
        hidePreviewResult();
    }

    function renderBuiltinSkillOverrides(items) {
        if (!personaBuiltinSkillList) return;
        if (!items.length) {
            personaBuiltinSkillList.innerHTML = '<div class="entity-card"><p class="entity-card__title">此角色尚無已授權的內建技能。</p></div>';
            return;
        }
        personaBuiltinSkillList.innerHTML = items.map(function (item) {
            return [
                '<article class="entity-card" data-builtin-skill-id="' + escapeHtml(item.skill_id) + '">',
                '  <div class="entity-card__head">',
                '    <div><p class="entity-card__title">' + escapeHtml(item.display_name) + '</p>' +
                '<p class="entity-card__meta">' + escapeHtml(item.skill_id) + '</p></div>',
                '  </div>',
                '  <p class="entity-card__meta">canonical trigger: ' + escapeHtml((item.canonical_triggers || []).join(', ') || '-') + '</p>',
                '  <input class="text-field persona-field" type="text" data-builtin-alias-input value="' + escapeHtml((item.aliases || []).join(', ')) + '" placeholder="別名（逗號分隔，例如：放歌, 來點音樂）">',
                '  <input class="text-field persona-field" type="number" min="0" data-builtin-priority-input value="' + escapeHtml(String(item.priority || 0)) + '" placeholder="priority（數字越大優先度越高）">',
                '  <div class="entity-card__actions">',
                '    <button class="secondary-button" type="button" data-builtin-override-save="' + escapeHtml(item.skill_id) + '">儲存別名／優先度</button>',
                '  </div>',
                '</article>'
            ].join('');
        }).join('');
    }

    function renderLocalSkillsForCustomization(items) {
        if (!personaLocalSkillList) return;
        if (!items.length) {
            personaLocalSkillList.innerHTML = '<div class="entity-card"><p class="entity-card__title">尚無角色專屬技能。</p></div>';
            return;
        }
        personaLocalSkillList.innerHTML = items.map(function (item) {
            return [
                '<article class="entity-card">',
                '  <div class="entity-card__head">',
                '    <div>' + skillCardTitleMeta(item) + '</div>',
                '  </div>',
                '  <p class="entity-card__meta">' + escapeHtml(item.description || '-') + '</p>',
                '  <p class="entity-card__meta">triggers: ' + escapeHtml((item.triggers || []).join(', ') || '-') + '</p>',
                '  <div class="entity-card__actions">',
                '    <button class="secondary-button" type="button" data-local-skill-edit="' + escapeHtml(item.skill_id) + '">編輯</button>',
                '    <button class="danger-button" type="button" data-local-skill-delete="' + escapeHtml(item.skill_id) + '">刪除</button>',
                '  </div>',
                '</article>'
            ].join('');
        }).join('');
    }

    function showLocalSkillForm(item) {
        editingLocalSkillId = item ? item.skill_id : null;
        if (localSkillIdInput) {
            localSkillIdInput.value = item ? item.skill_id : '';
            localSkillIdInput.disabled = Boolean(item);
        }
        setInputValue(localSkillDisplayNameInput, item ? item.display_name : '');
        setInputValue(localSkillDescriptionInput, item ? item.description : '');
        setInputValue(localSkillTriggersInput, item ? (item.triggers || []).join(', ') : '');
        setInputValue(localSkillBehaviorInput, item ? item.behavior : '');
        setInputValue(localSkillRequiredToolInput, item ? (item.required_tool || '') : '');
        if (localSkillFormError) {
            localSkillFormError.hidden = true;
            localSkillFormError.textContent = '';
        }
        if (personaLocalSkillForm) personaLocalSkillForm.hidden = false;
    }

    function hideLocalSkillForm() {
        editingLocalSkillId = null;
        if (personaLocalSkillForm) personaLocalSkillForm.hidden = true;
    }

    function showLocalSkillFormError(message) {
        if (!localSkillFormError) return;
        localSkillFormError.textContent = message;
        localSkillFormError.hidden = false;
    }

    function findLocalSkillById(skillId) {
        var list = (personaOriginal && personaOriginal.local_skills) || [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].skill_id === skillId) return list[i];
        }
        return null;
    }

    function saveLocalSkillForm() {
        if (!personaCharacterId) return;
        var skillId = ((localSkillIdInput && localSkillIdInput.value) || '').trim();
        if (!skillId) {
            showLocalSkillFormError('skill_id 為必填。');
            return;
        }
        var payload = {
            skill_id: skillId,
            display_name: localSkillDisplayNameInput ? localSkillDisplayNameInput.value.trim() : '',
            description: localSkillDescriptionInput ? localSkillDescriptionInput.value.trim() : '',
            triggers: ((localSkillTriggersInput && localSkillTriggersInput.value) || '')
                .split(',').map(function (item) { return item.trim(); }).filter(Boolean),
            behavior: localSkillBehaviorInput ? localSkillBehaviorInput.value.trim() : '',
            required_tool: localSkillRequiredToolInput ? localSkillRequiredToolInput.value.trim() : '',
        };
        callCharacterBridge('upsertLocalSkill', personaCharacterId, JSON.stringify(payload)).then(function (customization) {
            renderPersonaEditor(customization);
            setStatus('角色技能已儲存。', 'idle', 2400);
        }).catch(function (err) {
            showLocalSkillFormError(err.message);
        });
    }

    function deleteLocalSkillById(skillId) {
        if (!personaCharacterId) return;
        callCharacterBridge('deleteLocalSkill', personaCharacterId, skillId).then(function (customization) {
            renderPersonaEditor(customization);
            setStatus('角色技能已刪除。', 'idle', 2400);
        }).catch(function (err) {
            console.warn('[ECHOES UI] deleteLocalSkill failed:', err.message);
            setStatus('刪除技能失敗：' + err.message, 'error', 3200);
        });
    }

    function saveBuiltinOverride(skillId, card) {
        if (!personaCharacterId || !card) return;
        var aliasInput = card.querySelector('[data-builtin-alias-input]');
        var priorityInput = card.querySelector('[data-builtin-priority-input]');
        var aliases = ((aliasInput && aliasInput.value) || '')
            .split(',').map(function (item) { return item.trim(); }).filter(Boolean);
        var priority = Math.max(0, parseInt((priorityInput && priorityInput.value) || '0', 10) || 0);
        callCharacterBridge('saveSkillOverride', personaCharacterId, skillId, JSON.stringify(aliases), priority).then(function (customization) {
            renderPersonaEditor(customization);
            setStatus('別名／優先度已儲存。', 'idle', 2400);
        }).catch(function (err) {
            console.warn('[ECHOES UI] saveSkillOverride failed:', err.message);
            setStatus('儲存別名失敗：' + err.message, 'error', 3200);
        });
    }

    function savePersonaDraft() {
        if (!personaCharacterId) return;
        var value = personaTextarea ? personaTextarea.value : '';
        callCharacterBridge('savePersona', personaCharacterId, value).then(function (customization) {
            renderPersonaEditor(customization);
            setStatus('人設已儲存。', 'idle', 2400);
            refreshCharacterHud();
        }).catch(function (err) {
            console.warn('[ECHOES UI] savePersona failed:', err.message);
            setStatus('儲存人設失敗：' + err.message, 'error', 3200);
        });
    }

    function cancelPersonaDraft() {
        if (personaOriginal) renderPersonaEditor(personaOriginal);
    }

    function hidePreviewResult() {
        if (!personaPreviewResult) return;
        personaPreviewResult.hidden = true;
        personaPreviewResult.innerHTML = '';
    }

    function renderPreviewResult(diagnostics, errorMessage) {
        if (!personaPreviewResult) return;
        personaPreviewResult.hidden = false;
        if (errorMessage) {
            personaPreviewResult.innerHTML = '<p class="entity-card__title">預覽失敗</p><p class="entity-card__meta">' + escapeHtml(errorMessage) + '</p>';
            return;
        }
        if (!diagnostics || !diagnostics.matched) {
            personaPreviewResult.innerHTML = '<p class="entity-card__title">沒有命中的技能。</p>';
            return;
        }
        var candidates = (diagnostics.candidates || []).map(function (candidate) {
            return escapeHtml(candidate.skill_id) + '(' + escapeHtml(candidate.trigger) + ')';
        }).join('、');
        personaPreviewResult.innerHTML = [
            '<p class="entity-card__title">命中：' + escapeHtml(diagnostics.skill_id) + '</p>',
            '<p class="entity-card__meta">trigger: ' + escapeHtml(diagnostics.trigger) + ' · source: ' + escapeHtml(diagnostics.source) + '</p>',
            '<p class="entity-card__meta">候選：' + (candidates || '-') + '</p>'
        ].join('');
    }

    function previewSkillMatchText() {
        if (!personaCharacterId || !personaPreviewInput) return;
        var text = personaPreviewInput.value.trim();
        if (!text) return;
        callCharacterBridge('previewSkillMatch', personaCharacterId, text).then(function (diagnostics) {
            renderPreviewResult(diagnostics);
        }).catch(function (err) {
            renderPreviewResult(null, err.message);
        });
    }

    function loadPersonaEditor() {
        callCharacterBridge('getActiveState').then(function (state) {
            if (!state || state.active === false) {
                discardPersonaDraft();
                return null;
            }
            return callCharacterBridge('getCustomization', state.character_id);
        }).then(function (customization) {
            if (customization) renderPersonaEditor(customization);
        }).catch(function (err) {
            console.warn('[ECHOES UI] loadPersonaEditor failed:', err.message);
            setStatus('載入人設編輯器失敗：' + err.message, 'error', 3200);
        });
    }

    function wirePersonaEditor() {
        if (personaSaveButton) personaSaveButton.addEventListener('click', savePersonaDraft);
        if (personaCancelButton) personaCancelButton.addEventListener('click', cancelPersonaDraft);
        if (personaLocalSkillNewButton) {
            personaLocalSkillNewButton.addEventListener('click', function () { showLocalSkillForm(null); });
        }
        if (localSkillSaveButton) localSkillSaveButton.addEventListener('click', saveLocalSkillForm);
        if (localSkillFormCancelButton) localSkillFormCancelButton.addEventListener('click', hideLocalSkillForm);
        if (personaPreviewButton) personaPreviewButton.addEventListener('click', previewSkillMatchText);
        if (personaPreviewInput) {
            personaPreviewInput.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') previewSkillMatchText();
            });
        }
        if (personaBuiltinSkillList) {
            personaBuiltinSkillList.addEventListener('click', function (event) {
                var saveButton = event.target.closest('[data-builtin-override-save]');
                if (!saveButton) return;
                saveBuiltinOverride(saveButton.dataset.builtinOverrideSave, saveButton.closest('[data-builtin-skill-id]'));
            });
        }
        if (personaLocalSkillList) {
            personaLocalSkillList.addEventListener('click', function (event) {
                var editButton = event.target.closest('[data-local-skill-edit]');
                var deleteButton = event.target.closest('[data-local-skill-delete]');
                if (editButton) {
                    showLocalSkillForm(findLocalSkillById(editButton.dataset.localSkillEdit));
                    return;
                }
                if (deleteButton) deleteLocalSkillById(deleteButton.dataset.localSkillDelete);
            });
        }
    }

    function renderBackgroundStatus(background) {
        var status = background && background.status ? background.status : 'missing';
        console.log('[ECHOES UI] background=' + status);
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

    // ── Character Bridge 呼叫（async，帶 callback → Promise）─────

    function callCharacterBridge(method) {
        var args = Array.prototype.slice.call(arguments, 1);
        return new Promise(function (resolve, reject) {
            if (!characterBridge || typeof characterBridge[method] !== 'function') {
                reject(new Error('characterBridge not ready — cannot call: ' + method));
                return;
            }
            var callArgs = args.concat([function (resultJson) {
                var parsed;
                try {
                    parsed = JSON.parse(resultJson);
                } catch (err) {
                    reject(err);
                    return;
                }
                if (parsed && parsed.ok) {
                    resolve(parsed.data);
                } else {
                    reject(new Error((parsed && parsed.error) || ('unknown error from ' + method)));
                }
            }]);
            characterBridge[method].apply(characterBridge, callArgs);
        });
    }

    // ── UC05-1 HUD（Score/Lv pill，事件驅動 + 低頻輪詢）──────────

    function startHudPolling() {
        if (hudPollTimer) return;
        hudPollTimer = window.setInterval(refreshCharacterHud, 5000);
        refreshCharacterHud();
    }

    // ── App Screens 狀態機（UC01-1 / UC02-1 / UC03-1）────────────

    function showOverlay(screenId) {
        if (appScreens) appScreens.hidden = false;
        [screenMainMenu, screenPresetSelect, screenLoadSave].forEach(function (screen) {
            if (screen) screen.hidden = (screen.id !== screenId);
        });
        // 隱藏 UC05 遊戲 UI 元素（HUD / Dock / Live UI）
        document.body.classList.add('overlay-active');
        // 隱藏 Qt 拖曳層，讓 HTML 按鈕可接收點擊
        console.log('[ECHOES UI] calling setDragEnabled(false), type=' + (harnessBridge ? typeof harnessBridge.setDragEnabled : 'NO_BRIDGE'));
        callBridge('setDragEnabled', false);
        if (screenId === 'screen-main-menu') refreshMainMenu();
        if (screenId === 'screen-preset-select') loadPresetCarousel();
        if (screenId === 'screen-load-save') loadSaveGrid();
    }

    function hideOverlay() {
        if (appScreens) appScreens.hidden = true;
        document.body.classList.remove('overlay-active');
        callBridge('setDragEnabled', true);
    }

    function enterCompanionStage() {
        hideOverlay();
        refreshCharacterHud();
        callBridge('refreshState');
    }

    // ── UC02-1 Preset 聚光燈輪播 ───────────────────────────────

    function renderPresetCarousel() {
        var total = presetList.length;
        var current = total ? presetList[presetIndex] : null;
        setText(presetCarouselIndex, (total ? presetIndex + 1 : 0) + ' / ' + total);
        setText(presetName, current ? current.name : '尚無預設角色');
        setText(presetPersona, current ? (current.persona_description || '') : '[ 個性 / 簡介 ]');
        if (presetSelectButton) presetSelectButton.disabled = !current;

        if (presetThumbList) {
            var slots = [];
            for (var i = 0; i < 7; i++) {
                var preset = presetList[i];
                if (preset) {
                    slots.push(
                        '<button type="button" class="preset-thumb' + (i === presetIndex ? ' is-active' : '') + '" data-preset-index="' + i + '">' +
                        escapeHtml(preset.name) + '</button>'
                    );
                } else {
                    slots.push('<div class="preset-thumb preset-thumb--empty" aria-hidden="true"></div>');
                }
            }
            presetThumbList.innerHTML = slots.join('');
        }
    }

    function loadPresetCarousel() {
        callCharacterBridge('listPresets').then(function (presets) {
            presetList = Array.isArray(presets) ? presets : [];
            presetIndex = 0;
            renderPresetCarousel();
        }).catch(function (err) {
            console.warn('[ECHOES UI] listPresets failed:', err.message);
            presetList = [];
            renderPresetCarousel();
        });
    }

    function stepPreset(delta) {
        if (!presetList.length) return;
        presetIndex = (presetIndex + delta + presetList.length) % presetList.length;
        renderPresetCarousel();
    }

    function selectCurrentPreset() {
        var current = presetList[presetIndex];
        if (!current || !presetSelectButton) return;
        presetSelectButton.disabled = true;
        callCharacterBridge('createFromPreset', current.character_id, '').then(function () {
            enterCompanionStage();
        }).catch(function (err) {
            console.warn('[ECHOES UI] createFromPreset failed:', err.message);
            setStatus('建立角色失敗：' + err.message, 'error', 4800);
        }).then(function () {
            presetSelectButton.disabled = false;
        });
    }

    // ── UC03-1 Load Save ──────────────────────────────────────

    function updateSaveActionButtons() {
        var selectedItem = null;
        for (var i = 0; i < saveList.length; i++) {
            if (saveList[i].character_id === selectedSaveId) {
                selectedItem = saveList[i];
                break;
            }
        }
        if (saveContinueButton) saveContinueButton.disabled = !selectedItem;
        if (saveDeleteButton) {
            var isPreset = Boolean(selectedItem && selectedItem.is_preset);
            saveDeleteButton.disabled = !selectedItem || isPreset;
            saveDeleteButton.hidden = isPreset;
        }
    }

    function continueSelectedSave() {
        if (!selectedSaveId) return;
        callCharacterBridge('switchCharacter', selectedSaveId).then(function () {
            enterCompanionStage();
        }).catch(function (err) {
            console.warn('[ECHOES UI] switchCharacter failed:', err.message);
            setStatus('載入存檔失敗：' + err.message, 'error', 4800);
        });
    }

    function deleteSelectedSave() {
        if (!selectedSaveId) return;
        callCharacterBridge('deleteCharacter', selectedSaveId).then(function () {
            selectedSaveId = null;
            loadSaveGrid();
        }).catch(function (err) {
            console.warn('[ECHOES UI] deleteCharacter failed:', err.message);
            setStatus('刪除存檔失敗：' + err.message, 'error', 4800);
        });
    }

    // ── App Screens 事件綁定 ──────────────────────────────────

    function setupAppScreens() {
        if (menuCreateButton) {
            menuCreateButton.addEventListener('click', function () {
                showOverlay('screen-preset-select');
            });
        }
        if (menuLoadButton) {
            menuLoadButton.addEventListener('click', function () {
                if (menuLoadButton.disabled) return;
                showOverlay('screen-load-save');
            });
        }
        if (menuSettingsButton) {
            menuSettingsButton.addEventListener('click', function () {
                setStatus('Settings 尚未串接（本次變更範圍外）。', 'idle', 2400);
            });
        }
        if (menuQuitButton) {
            menuQuitButton.addEventListener('click', function () {
                callBridge('triggerOverlayAction', 'quit');
            });
        }
        if (presetCarouselPrev) {
            presetCarouselPrev.addEventListener('click', function () { stepPreset(-1); });
        }
        if (presetCarouselNext) {
            presetCarouselNext.addEventListener('click', function () { stepPreset(1); });
        }
        if (presetSelectButton) {
            presetSelectButton.addEventListener('click', selectCurrentPreset);
        }
        if (presetCustomizeToggle && presetCustomizePanel) {
            presetCustomizeToggle.addEventListener('click', function () {
                presetCustomizePanel.hidden = !presetCustomizePanel.hidden;
            });
        }
        if (presetCustomizeGenerate) {
            presetCustomizeGenerate.addEventListener('click', function () {
                var current = presetList[presetIndex];
                var label = current ? current.name : '預設角色';
                setStatus('已套用 ' + label + ' 的預設造型（佔位邏輯，尚未接真實產圖）。', 'idle', 3200);
            });
        }
        if (presetThumbList) {
            presetThumbList.addEventListener('click', function (event) {
                var thumb = event.target.closest('[data-preset-index]');
                if (!thumb) return;
                presetIndex = Number(thumb.dataset.presetIndex) || 0;
                renderPresetCarousel();
            });
        }
        if (presetBackButton) {
            presetBackButton.addEventListener('click', function () { showOverlay('screen-main-menu'); });
        }
        if (saveCardGrid) {
            saveCardGrid.addEventListener('click', function (event) {
                var addCard = event.target.closest('[data-save-add]');
                if (addCard) {
                    showOverlay('screen-preset-select');
                    return;
                }
                var card = event.target.closest('[data-save-id]');
                if (!card) return;
                selectedSaveId = card.dataset.saveId;
                renderSaveGrid();
            });
        }
        if (saveContinueButton) {
            saveContinueButton.addEventListener('click', continueSelectedSave);
        }
        if (saveDeleteButton) {
            saveDeleteButton.addEventListener('click', deleteSelectedSave);
        }
        if (saveBackButton) {
            saveBackButton.addEventListener('click', function () { showOverlay('screen-main-menu'); });
        }

        setupWindowDragHandles();
        setupCompanionDock();
    }

    function setupWindowDragHandles() {
        document.querySelectorAll('.window-drag-handle').forEach(function (handle) {
            handle.addEventListener('mousedown', function (event) {
                if (event.button !== 0) return;
                if (event.target.closest('button, input, a, textarea, select')) return;
                callBridge('beginWindowDrag');
            });
        });
    }

    // ── UC05-1 Companion Dock（Talk / Agent / Style / Scene）────

    function collapseCompanionDock() {
        if (companionDockPanel) companionDockPanel.hidden = true;
        dockPanels.forEach(function (panel) { panel.hidden = true; });
        dockButtons.forEach(function (button) { button.classList.remove('is-active'); });
        // 離開 Persona 面板(收合 dock、切換其他分頁、返回主選單)一律視為取消草稿。
        discardPersonaDraft();
    }

    function activateCompanionDock(button) {
        var targetId = button.dataset.dockPanel;
        var alreadyActive = button.classList.contains('is-active');
        collapseCompanionDock();
        if (alreadyActive) return;
        dockButtons.forEach(function (candidate) {
            candidate.classList.toggle('is-active', candidate === button);
        });
        var targetPanel = document.getElementById(targetId);
        if (targetPanel) targetPanel.hidden = false;
        if (companionDockPanel) companionDockPanel.hidden = false;
        if (targetId === 'dock-panel-persona') loadPersonaEditor();
    }

    function sendTalkText() {
        if (!talkTextInput) return;
        var text = talkTextInput.value.trim();
        if (!text) return;

        // 文字提交只傳 text;Provider 由全域 runtime 設定,前端不可覆寫。
        callBridge('sendText', text);
        talkTextInput.value = '';
    }

    // ── 事件綁定 ──────────────────────────────────────────────

    function setupForms() {
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
        if (skillList) {
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
        if (characterSkillList) {
            characterSkillList.addEventListener('click', function (event) {
                var toggle = event.target.closest('[data-character-skill-toggle]');
                var trigger = event.target.closest('[data-skill-trigger]');
                if (toggle) {
                    callBridge('toggleSkill', toggle.dataset.characterSkillToggle, toggle.dataset.enabled === 'true');
                    return;
                }
                if (!trigger) return;
                var skillId = trigger.dataset.skillTrigger;
                trigger.disabled = true;
                callCharacterBridge('triggerSkill', skillId).then(function () {
                    refreshCharacterHud();
                }).catch(function (err) {
                    console.warn('[ECHOES UI] triggerSkill failed:', err.message);
                    setStatus('技能觸發失敗：' + err.message, 'error', 3200);
                }).then(function () {
                    trigger.disabled = false;
                });
            });
        }
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
            characterBridge = channel.objects.characterBridge || null;
            if (!harnessBridge) {
                console.error('[ECHOES UI] harnessBridge object not found in channel.objects');
                setStatus('Bridge object missing. Check PyQt registration.', 'error', 0);
                return;
            }
            if (!characterBridge) {
                console.warn('[ECHOES UI] characterBridge object not found in channel.objects');
            }
            console.log('[ECHOES UI] bridge=ready');
            setStatus('Bridge ready.', 'idle', 1800);
            callBridge('setDragEnabled', false);
            callBridge('refreshState');
            refreshMainMenu();
            showOverlay('screen-main-menu');
            startHudPolling();
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

    window.moveCharacter = function (x, y, scale) {
        document.documentElement.style.setProperty('--pet-anchor-shift', Number(x || 0) + 'px');
        document.documentElement.style.setProperty('--pet-floor-offset', Number(y || 0) + 'px');
        if (scale != null) {
            document.documentElement.style.setProperty('--pet-scale', String(scale));
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
        // 以原生 loop 保持同一段 WebM；Python 只有在同輪 TTS 結束後才會停止它。
        setSource(source, true);
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
        if (motionLoopActive && motionLoopSource) {
            window.playTemporaryVideo(motionLoopSource);
            return;
        }
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

    function refreshMainMenu() {
        if (!menuLoadButton) return;
        callCharacterBridge('listCharacters').then(function (characters) {
            var list = Array.isArray(characters) ? characters : [];
            var hasCharacters = list.length > 0;
            menuLoadButton.disabled = !hasCharacters;
            menuLoadButton.querySelector('.menu-item__sub').textContent = hasCharacters ? (list.length + ' 個角色') : '尚無角色';
        }).catch(function (err) {
            console.log('[ECHOES UI] listCharacters FAILED: ' + err.message);
            menuLoadButton.disabled = true;
            menuLoadButton.querySelector('.menu-item__sub').textContent = '尚無角色';
        });
    }

    function sortByLastPlayedDesc(items) {
        return items.slice().sort(function (a, b) {
            return (b.last_played_at || '').localeCompare(a.last_played_at || '');
        });
    }

    function renderSaveGrid() {
        if (!saveCardGrid) return;
        var cards = saveList.map(function (item) {
            var selected = item.character_id === selectedSaveId;
            var thumbStyle = '';
            if (item.background_image) {
                thumbStyle = ' style="background-image:url(\'' + escapeHtml(normalizeProjectAssetSource(item.background_image)) + '\')"';
            }
            return '<button type="button" class="save-card' + (selected ? ' is-selected' : '') + '" data-save-id="' + escapeHtml(item.character_id) + '">' +
                '<span class="save-card__thumb" aria-hidden="true"' + thumbStyle + '></span>' +
                '<span class="save-card__meta">' +
                '<span class="save-card__name">' + escapeHtml(item.name) + ' · Lv.' + escapeHtml(item.level) + '</span>' +
                '<span class="save-card__sub">' + escapeHtml(formatPlaytime(item.playtime_seconds)) + ' · ' + escapeHtml(formatLastPlayed(item.last_played_at)) + '</span>' +
                '</span>' +
                '</button>';
        });
        cards.push(
            '<button type="button" class="save-card save-card--empty" data-save-add="true">' +
            '<span aria-hidden="true">+</span>' +
            '<span>新增角色</span>' +
            '</button>'
        );
        saveCardGrid.innerHTML = cards.join('');
        updateSaveActionButtons();
    }

    function loadSaveGrid() {
        selectedSaveId = null;
        callCharacterBridge('listCharacters').then(function (characters) {
            saveList = sortByLastPlayedDesc(Array.isArray(characters) ? characters : []);
            renderSaveGrid();
        }).catch(function (err) {
            console.warn('[ECHOES UI] listCharacters failed:', err.message);
            saveList = [];
            renderSaveGrid();
        });
    }

    function showHudDelta(delta) {
        if (!hudScoreDelta) return;
        if (hudDeltaTimer) {
            window.clearTimeout(hudDeltaTimer);
            hudDeltaTimer = null;
        }
        var normalized = Number(delta) || 0;
        if (normalized > 0) {
            hudScoreDelta.hidden = false;
            hudScoreDelta.textContent = '+' + normalized;
            hudDeltaTimer = window.setTimeout(function () {
                hudScoreDelta.hidden = true;
                hudScoreDelta.textContent = '';
            }, 2400);
        } else {
            hudScoreDelta.hidden = true;
            hudScoreDelta.textContent = '';
        }
    }

    function renderCharacterHud(state, xpDeltaOverride) {
        if (!hudScore) return;
        if (!state || state.active === false) {
            hudScore.hidden = true;
            latestHudState = null;
            return;
        }
        latestHudState = state;
        var xpState = state.xp || {};
        var xpTotal = Number(xpState.xp_total != null ? xpState.xp_total : state.xp_total) || 0;
        var level = Number(xpState.level != null ? xpState.level : state.level) || 1;
        var progressPercent = Number(
            xpState.progress_percent != null
                ? xpState.progress_percent
                : (state.progress_percent != null ? state.progress_percent : 0)
        ) || 0;
        hudScore.hidden = false;
        if (hudScoreText) hudScoreText.textContent = 'Score ' + xpTotal;
        if (hudScoreLevel) hudScoreLevel.textContent = 'Lv.' + level;
        if (hudScoreProgressFill) hudScoreProgressFill.style.width = Math.max(0, Math.min(100, progressPercent)) + '%';
        showHudDelta(xpDeltaOverride != null ? xpDeltaOverride : xpState.last_delta);
    }

    function renderLatestAgentEvent(eventPayload, fallbackDelta) {
        var eventData = eventPayload || {};
        var rewardSummary = eventData.reward_summary || {};
        var reply = eventData.reply
            || eventData.message
            || eventData.summary
            || rewardSummary.summary
            || rewardSummary.display
            || '輸入問題或點選快捷指令。';
        var delta = eventData.xp_delta;
        if (delta == null) delta = fallbackDelta;
        setAgentResult(reply, delta);
    }

    function renderState(state) {
        if (!state) return;
        latestRuntimeState = state;
        renderBackgroundStatus(state.background || null);
        renderCharacterHud({ active: true, xp: state.xp || {} }, state.xp && state.xp.last_delta);
        renderLatestAgentEvent(state.latest_event || null, state.xp && state.xp.last_delta);
    }

    function refreshCharacterHud() {
        callCharacterBridge('getActiveState').then(function (state) {
            var mergedState = state || {};
            if (latestRuntimeState && latestRuntimeState.xp) {
                mergedState.xp = latestRuntimeState.xp;
            }
            renderCharacterHud(mergedState, latestRuntimeState && latestRuntimeState.xp ? latestRuntimeState.xp.last_delta : 0);
        }).catch(function (err) {
            console.warn('[ECHOES UI] getActiveState failed:', err.message);
        });
    }

    window.hydrateAgenticUI = function (payload) {
        payload = payload || {};
        renderState(payload.state || null);
        renderRuntimeControls(payload.runtimeControls || null);
        renderSkills(payload.skills || []);
        renderCharacterSkills(payload.skills || []);
        if (payload.message) {
            setStatus(payload.message, payload.tone || 'idle', payload.timeoutMs || 0);
        }
        renderCharacterHud(
            payload.state ? { active: true, xp: payload.state.xp || {}, progress_percent: payload.progress_percent } : null,
            payload.xp_delta
        );
        var eventData = payload.event || (payload.state && payload.state.latest_event);
        renderLatestAgentEvent(eventData, payload.xp_delta);
    };

    function sendAgentCommandText() {
        if (!agentCommandInput) return;
        var text = agentCommandInput.value.trim();
        if (!text) return;

        // 文字提交只傳 text;Provider 由全域 runtime 設定,前端不可覆寫。
        callBridge('sendText', text);
        agentCommandInput.value = '';
    }

    function setupCompanionDock() {
        wirePersonaEditor();
        dockButtons.forEach(function (button) {
            button.addEventListener('click', function () { activateCompanionDock(button); });
        });
        if (talkSendButton) {
            talkSendButton.addEventListener('click', sendTalkText);
        }
        if (talkTextInput) {
            talkTextInput.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') sendTalkText();
            });
        }
        if (agentCommandSubmit) {
            agentCommandSubmit.addEventListener('click', sendAgentCommandText);
        }
        if (agentCommandInput) {
            agentCommandInput.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') sendAgentCommandText();
            });
        }
        if (agentCommandMic) {
            agentCommandMic.addEventListener('click', function () {
                callBridge('toggleStt');
            });
        }
        if (agentChipJoke) {
            agentChipJoke.addEventListener('click', function () {
                callBridge('triggerQuickIntent', 'joke');
            });
        }
        if (agentChipMusic) {
            agentChipMusic.addEventListener('click', function () {
                callBridge('triggerOverlayAction', 'play_music');
            });
        }
        if (agentChipNews) {
            agentChipNews.addEventListener('click', function () {
                callBridge('triggerOverlayAction', 'report_news');
            });
        }
        if (companionSettingsButton) {
            companionSettingsButton.addEventListener('click', function () {
                setStatus('Settings 版位保留，後續會沿用既有流程。', 'idle', 2400);
            });
        }
        if (companionLeaveButton) {
            companionLeaveButton.addEventListener('click', function () {
                collapseCompanionDock();
                showOverlay('screen-main-menu');
            });
        }
    }

    try {
        updateStageScale();
        if (typeof ResizeObserver !== 'undefined') {
            resizeObserver = new ResizeObserver(function () {
                updateStageScale();
            });
            resizeObserver.observe(document.documentElement);
        } else {
            window.addEventListener('resize', updateStageScale);
        }
        setupForms();
        wireDynamicActions();
        setupAppScreens();
        setupWebChannel();
        setStatus('', 'idle', 0);
        window.setConversationQueueDepth(0);
        window.setRuntimeMode('harness');
    } catch (error) {
        console.error('[ECHOES APP.JS] init error:', error.message);
    }

    console.log('[ECHOES APP.JS] init complete — harnessBridge:', typeof harnessBridge);
})();
