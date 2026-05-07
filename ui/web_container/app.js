/**
 * ECHOES — WebM 熱切換控制器
 * 提供 changeVideo(filename) 給 Python (PyQt) 透過 runJavaScript 呼叫
 */

(function () {
    'use strict';

    var video = document.getElementById('pet-video');
    var panelVideo = document.getElementById('panel-video');
    var character = document.getElementById('pet-character');
    var audio = document.getElementById('room-audio');
    var roomCharacterName = document.getElementById('room-character-name');
    var actionStatus = document.getElementById('action-status');
    var actionStatusText = document.getElementById('action-status-text');
    var conversationList = document.getElementById('conversation-list');
    var conversationQueueText = document.getElementById('conversation-queue-text');
    var idleSource = '';
    var idleMotionCandidates = [];
    var statusTimer = null;
    var motionLoopTimer = null;
    var motionLoopSource = null;
    var motionLoopActive = false;
    var motionLoopGeneration = 0;
    var mainVideoGeneration = 0;
    var panelVideoGeneration = 0;
    var defaultStatusText = '房間待命中';
    var conversationTurns = new Map();
    var maxConversationTurns = 3;
    var characterOffsetX = 0;
    var characterOffsetY = 0;
    var characterScale = 1;

    function updateCharacterTransform() {
        var target = character || video;

        if (!target) {
            console.warn('[ECHOES] 找不到角色容器，無法更新角色 transform');
            return;
        }

        target.style.transform = (
            'translate3d(' + characterOffsetX + 'px, ' + characterOffsetY + 'px, 0) ' +
            'scale(' + characterScale + ')'
        );
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
        userLabel.textContent = sourceLabel || '使用者';
        var userText = document.createElement('p');
        userText.className = 'conversation-turn__text';
        userRow.appendChild(userLabel);
        userRow.appendChild(userText);

        var assistantRow = document.createElement('div');
        assistantRow.className = 'conversation-turn__row';
        var assistantLabel = document.createElement('p');
        assistantLabel.className = 'conversation-turn__label';
        assistantLabel.textContent = 'ECHOES';
        var assistantText = document.createElement('p');
        assistantText.className = 'conversation-turn__text conversation-turn__text--muted';
        assistantText.textContent = '等待回應中...';
        assistantRow.appendChild(assistantLabel);
        assistantRow.appendChild(assistantText);

        article.appendChild(userRow);
        article.appendChild(assistantRow);
        conversationList.appendChild(article);

        var turn = {
            root: article,
            userText: userText,
            assistantText: assistantText
        };
        conversationTurns.set(turnId, turn);
        trimConversationTurns();
        return turn;
    }

    function trimConversationTurns() {
        while (conversationList.children.length > maxConversationTurns) {
            var firstChild = conversationList.firstElementChild;
            if (!firstChild) {
                break;
            }
            conversationTurns.delete(firstChild.dataset.turnId);
            conversationList.removeChild(firstChild);
        }
    }

    function setSource(source, shouldLoop) {
        if (!source || typeof source !== 'string') {
            console.warn('[ECHOES] 無效的影片來源:', source);
            return;
        }

        mainVideoGeneration += 1;
        var requestGeneration = mainVideoGeneration;
        video.muted = true;
        video.defaultMuted = true;
        video.playsInline = true;
        video.autoplay = true;
        video.setAttribute('muted', '');
        video.setAttribute('playsinline', '');
        video.loop = Boolean(shouldLoop);
        video.dataset.requestGeneration = String(requestGeneration);
        video.src = source;
        video.load();
        var playPromise = video.play();
        if (playPromise !== undefined) {
            playPromise.then(function () {
                if (requestGeneration !== mainVideoGeneration) {
                    return;
                }
                console.log('[JS] 影片開始播放:', source);
            }).catch(function (error) {
                if (requestGeneration !== mainVideoGeneration) {
                    return;
                }
                console.error('[JS] 播放被 Chromium 阻擋, Reason:', error.name, error.message);
            });
        }
    }

    function normalizeIdleMotionCandidates(candidates) {
        if (!Array.isArray(candidates)) {
            return [];
        }

        return candidates.map(function (candidate) {
            if (!candidate || typeof candidate !== 'object') {
                return null;
            }
            var source = typeof candidate.source === 'string' ? candidate.source : '';
            var weight = Number(candidate.weight) || 1;
            if (!source) {
                return null;
            }
            return {
                source: source,
                weight: Math.max(1, weight)
            };
        }).filter(Boolean);
    }

    function chooseIdleMotionSource(fallbackSource) {
        var candidates = idleMotionCandidates;
        if (!candidates.length) {
            return fallbackSource || idleSource;
        }

        var totalWeight = candidates.reduce(function (sum, candidate) {
            return sum + candidate.weight;
        }, 0);
        if (totalWeight <= 0) {
            return candidates[0].source;
        }

        var threshold = Math.random() * totalWeight;
        var runningWeight = 0;
        for (var index = 0; index < candidates.length; index += 1) {
            runningWeight += candidates[index].weight;
            if (threshold < runningWeight) {
                return candidates[index].source;
            }
        }
        return candidates[candidates.length - 1].source;
    }

    function restoreIdleMotionInternal(fallbackSource) {
        var nextIdleSource = chooseIdleMotionSource(fallbackSource);
        if (!nextIdleSource) {
            console.warn('[ECHOES] 目前沒有可用的 idle motion candidate');
            return;
        }

        idleSource = nextIdleSource;
        console.log('[ECHOES] 設定 idle 動畫:', nextIdleSource);
        if (motionLoopActive) {
            return;
        }
        setSource(nextIdleSource, true);
    }

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

    video.onerror = function (event) {
        var err = video.error;
        var errCode = err ? err.code : 'unknown';
        var errMsg = err ? err.message : '(no details)';
        console.error('[ECHOES] 影片載入失敗 src=' + video.src + ' code=' + errCode + ' msg=' + errMsg);
    };

    video.addEventListener('ended', function () {
        console.log('[ECHOES:MAIN_VIDEO_ENDED]');
        if (!video.loop && !motionLoopActive) {
            restoreIdleMotionInternal(idleSource);
        }
    });

    audio.addEventListener('ended', function () {
        console.log('[ECHOES:ROOM_AUDIO_ENDED]');
        if (audio.dataset.statusManaged === 'true') {
            setStatus('音樂播放完畢', 'idle', 2200);
        }
    });

    audio.addEventListener('error', function () {
        console.warn('[ECHOES] 音訊載入失敗:', audio.src);
        setStatus('音訊載入失敗', 'error', 4200);
    });

    /**
     * 設定目前角色的 idle 動畫。
     * @param {string} source - 影片來源 URL
     */
    window.setIdleVideo = function (source) {
        idleSource = source;
        console.log('[ECHOES] 設定 idle 動畫:', source);
        setSource(source, true);
    };

    window.setIdleMotionCandidates = function (candidates) {
        idleMotionCandidates = normalizeIdleMotionCandidates(candidates);
        console.log('[ECHOES] 更新 idle motion candidates:', idleMotionCandidates.length);
    };

    window.restoreIdleMotion = function (fallbackSource) {
        restoreIdleMotionInternal(fallbackSource || idleSource);
    };

    /**
     * 播放一次性動作，結束後自動回到 idle。
     * @param {string} source - 影片來源 URL
     */
    window.playTemporaryVideo = function (source) {
        var targetVideo = document.getElementById('pet-video');
        if (!targetVideo) {
            console.error('[JS ERROR] 找不到影片元素 #pet-video，無法播放動作');
            return;
        }

        targetVideo.muted = true;
        targetVideo.defaultMuted = true;
        targetVideo.playsInline = true;
        targetVideo.autoplay = true;
        targetVideo.setAttribute('muted', '');
        targetVideo.setAttribute('playsinline', '');
        targetVideo.loop = false;

        mainVideoGeneration += 1;
        var requestGeneration = mainVideoGeneration;
        console.log('[JS] 準備切換動作:', source);
        targetVideo.pause();
        targetVideo.dataset.requestGeneration = String(requestGeneration);
        targetVideo.src = source;
        targetVideo.load();

        var playPromise = targetVideo.play();
        if (playPromise !== undefined) {
            playPromise.then(function () {
                if (requestGeneration !== mainVideoGeneration) {
                    return;
                }
                console.log('[JS] 動作播放成功:', source);
            }).catch(function (error) {
                if (requestGeneration !== mainVideoGeneration) {
                    return;
                }
                console.error('[JS ERROR] 動作切換失敗:', error.name, error.message);
            });
        }
    };

    window.moveCharacter = function (x, y, scale) {
        characterOffsetX = Number(x) || 0;
        characterOffsetY = Number(y) || 0;
        characterScale = Number(scale) || 1;
        updateCharacterTransform();
        console.log('[ECHOES] 角色 transform:', { x: characterOffsetX, y: characterOffsetY, scale: characterScale });
    };

    window.setCharacterObjectPosition = function (objectPosition) {
        var value = String(objectPosition || 'center bottom');
        video.style.objectPosition = value;
        console.log('[ECHOES] 角色 object-position:', value);
    };

    window.setActionStatus = function (message, tone, timeoutMs) {
        setStatus(message, tone, Number(timeoutMs) || 0);
    };

    window.clearActionStatus = function () {
        setStatus('', 'idle', 0);
    };

    window.setRoomCharacter = function (name) {
        roomCharacterName.textContent = name || '未選擇角色';
    };

    window.beginConversationTurn = function (turnId, sourceLabel, userText) {
        if (!turnId) {
            return;
        }
        var turn = ensureConversationTurn(String(turnId), sourceLabel || '使用者');
        turn.root.dataset.state = 'active';
        turn.userText.textContent = userText || '';
        turn.assistantText.textContent = '等待回應中...';
        turn.assistantText.classList.add('conversation-turn__text--muted');
        conversationList.appendChild(turn.root);
        trimConversationTurns();
    };

    window.appendConversationAssistant = function (turnId, fragment) {
        if (!turnId || !fragment) {
            return;
        }
        var turn = ensureConversationTurn(String(turnId), '使用者');
        if (turn.assistantText.classList.contains('conversation-turn__text--muted')) {
            turn.assistantText.textContent = '';
            turn.assistantText.classList.remove('conversation-turn__text--muted');
        }
        turn.assistantText.textContent += String(fragment);
    };

    window.finishConversationTurn = function (turnId) {
        if (!turnId) {
            return;
        }
        var turn = ensureConversationTurn(String(turnId), '使用者');
        turn.root.dataset.state = 'done';
        if (!turn.assistantText.textContent) {
            turn.assistantText.textContent = '本輪沒有可顯示的回覆。';
            turn.assistantText.classList.add('conversation-turn__text--muted');
        }
    };

    window.setConversationQueueDepth = function (queueDepth) {
        var depth = Number(queueDepth) || 0;
        conversationQueueText.textContent = '佇列 ' + depth;
    };

    window.clearConversationTurns = function () {
        conversationTurns.clear();
        while (conversationList.firstElementChild) {
            conversationList.removeChild(conversationList.firstElementChild);
        }
        window.setConversationQueueDepth(0);
    };

    window.playRoomAudio = function (source, title, updateStatus) {
        if (!source || typeof source !== 'string') {
            console.warn('[ECHOES] 無效的音訊來源:', source);
            setStatus('找不到可播放音訊', 'warn', 3200);
            return;
        }

        var shouldUpdateStatus = updateStatus !== false;
        audio.dataset.statusManaged = shouldUpdateStatus ? 'true' : 'false';
        audio.pause();
        audio.src = source;
        audio.load();
        audio.play().then(function () {
            if (shouldUpdateStatus) {
                setStatus(title ? '正在播放: ' + title : '音樂播放中', 'music', 0);
            }
        }).catch(function (err) {
            console.warn('[ECHOES] 音樂播放失敗:', err.message);
            setStatus('音樂播放失敗: ' + err.message, 'error', 4800);
        });
    };

    window.stopRoomAudio = function () {
        audio.pause();
        audio.dataset.statusManaged = 'false';
        audio.removeAttribute('src');
        audio.load();
    };

    panelVideo.addEventListener('ended', function () {
        console.log('[ECHOES:PANEL_ENDED]');
    });

    window.playPanelVideo = function (source, shouldLoop, muted) {
        if (!source || !panelVideo) {
            return;
        }
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
            console.warn('[ECHOES] panel video 播放失敗:', err.message);
        });
    };

    window.setPanelVideoMuted = function (muted) {
        if (!panelVideo) {
            return;
        }
        panelVideo.muted = (muted !== false);
    };

    window.clearPanelVideo = function () {
        if (!panelVideo) {
            return;
        }
        panelVideoGeneration += 1;
        panelVideo.pause();
        panelVideo.removeAttribute('src');
        panelVideo.load();
        panelVideo.style.display = 'none';
    };

    window.startMotionLoop = function (source, intervalMs) {
        window.stopMotionLoop();
        if (!source) { return; }
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
        console.log('[ECHOES] motionLoop 啟動:', source, 'interval=', intervalMs || 1000);
    };

    window.stopMotionLoop = function () {
        motionLoopGeneration += 1;
        if (motionLoopTimer) {
            clearInterval(motionLoopTimer);
            motionLoopTimer = null;
        }
        motionLoopSource = null;
        motionLoopActive = false;
        console.log('[ECHOES] motionLoop 停止');
    };

    window.resetRoomState = function () {
        window.stopRoomAudio();
        window.clearPanelVideo();
        window.stopMotionLoop();
        window.clearConversationTurns();
        window.clearActionStatus();
        if (idleSource) {
            restoreIdleMotionInternal(idleSource);
        }
    };

    window.setRoomBackground = function (source) {
        if (!source) {
            return;
        }
        var bg = document.querySelector('img.room-background');
        if (bg) {
            bg.src = source;
        }
    };

    // 舊版橋接函式保留，避免其他模組呼叫失敗。
    window.changeVideo = function (source) {
        window.setIdleVideo(source);
    };

    /**
     * 取得目前播放狀態（供 Python 診斷用）
     * @returns {object}
     */
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

    setStatus('', 'idle', 0);
    window.setConversationQueueDepth(0);
})();
