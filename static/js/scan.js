(function () {
    const qrReader = document.getElementById('qr-reader');
    if (!qrReader || typeof Html5Qrcode === 'undefined') {
        return;
    }

    const qr = new Html5Qrcode('qr-reader');
    const config = { fps: 10, qrbox: { width: 250, height: 250 } };

    const onScanSuccess = (decodedText) => {
        const input = document.querySelector('input[name="token"]');
        if (input) {
            input.value = decodedText.trim();
        }
        qr.stop().catch(() => {});
    };

    qr.start({ facingMode: 'environment' }, config, onScanSuccess).catch((error) => {
        console.warn('QR scanner error:', error);
    });
})();
