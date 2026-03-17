/**
 * ECHOES — WebM 熱切換控制器
 * 提供 changeVideo(filename) 給 Python (PyQt) 透過 runJavaScript 呼叫
 */

(function () {
    'use strict';

    var ASSET_BASE = '../../assets/webm/';
    var video = document.getElementById('pet-video');

    // 預設載入 idle.webm（若存在）
    video.src = ASSET_BASE + 'idle.webm';

    video.addEventListener('error', function () {
        console.warn('[ECHOES] 影片載入失敗:', video.src);
    });

    /**
     * 切換影片來源
     * @param {string} filename - 純檔名，例如 "happy.webm"
     */
    window.changeVideo = function (filename) {
        if (!filename || typeof filename !== 'string') {
            console.warn('[ECHOES] changeVideo: 無效的檔名', filename);
            return;
        }
        var newSrc = ASSET_BASE + filename;
        console.log('[ECHOES] 切換影片:', newSrc);
        video.src = newSrc;
        video.load();
        video.play().catch(function (err) {
            console.warn('[ECHOES] 播放失敗:', err.message);
        });
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
