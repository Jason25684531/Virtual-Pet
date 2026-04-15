/**
 * ECHOES — WebM 熱切換控制器
 * 提供 changeVideo(filename) 給 Python (PyQt) 透過 runJavaScript 呼叫
 */

(function () {
    'use strict';

    var video = document.getElementById('pet-video');
    var character = document.getElementById('pet-character');
    var audio = document.getElementById('room-audio');
    var roomCharacterName = document.getElementById('room-character-name');
    var actionStatus = document.getElementById('action-status');
    var actionStatusText = document.getElementById('action-status-text');
    var idleSource = '';
    var statusTimer = null;
    var defaultStatusText = '房間待命中';

    function setSource(source, shouldLoop) {
        if (!source || typeof source !== 'string') {
            console.warn('[ECHOES] 無效的影片來源:', source);
            return;
        }

        video.loop = Boolean(shouldLoop);
        video.src = source;
        video.load();
        video.play().catch(function (err) {
            console.warn('[ECHOES] 播放失敗:', err.message);
        });
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

    video.addEventListener('error', function () {
        console.warn('[ECHOES] 影片載入失敗:', video.src);
    });

    video.addEventListener('ended', function () {
        if (!video.loop && idleSource) {
            setSource(idleSource, true);
        }
    });

    audio.addEventListener('ended', function () {
        setStatus('音樂播放完畢', 'idle', 2200);
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

    /**
     * 播放一次性動作，結束後自動回到 idle。
     * @param {string} source - 影片來源 URL
     */
    window.playTemporaryVideo = function (source) {
        console.log('[ECHOES] 播放單次動作:', source);
        setSource(source, false);
    };

    window.moveCharacter = function (x, y) {
        var target = character || video;

        if (!target) {
            console.warn('[ECHOES] 找不到角色容器，無法移動角色');
            return;
        }

        target.style.transform = 'translate3d(' + Number(x) + 'px, ' + Number(y) + 'px, 0)';
        console.log('[ECHOES] 角色位移:', { x: Number(x), y: Number(y) });
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

    window.playRoomAudio = function (source, title) {
        if (!source || typeof source !== 'string') {
            console.warn('[ECHOES] 無效的音訊來源:', source);
            setStatus('找不到可播放音訊', 'warn', 3200);
            return;
        }

        audio.pause();
        audio.src = source;
        audio.load();
        audio.play().then(function () {
            setStatus(title ? '正在播放: ' + title : '音樂播放中', 'music', 0);
        }).catch(function (err) {
            console.warn('[ECHOES] 音樂播放失敗:', err.message);
            setStatus('音樂播放失敗: ' + err.message, 'error', 4800);
        });
    };

    window.stopRoomAudio = function () {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
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
})();
