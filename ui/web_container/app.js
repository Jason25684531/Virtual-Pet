/**
 * ECHOES — WebM 熱切換控制器
 * 提供 changeVideo(filename) 給 Python (PyQt) 透過 runJavaScript 呼叫
 */

(function () {
    'use strict';

    var video = document.getElementById('pet-video');
    var idleSource = '';

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

    video.addEventListener('error', function () {
        console.warn('[ECHOES] 影片載入失敗:', video.src);
    });

    video.addEventListener('ended', function () {
        if (!video.loop && idleSource) {
            setSource(idleSource, true);
        }
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
            readyState: video.readyState
        };
    };
})();
