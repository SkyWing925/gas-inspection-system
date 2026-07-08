/* ===== CANVAS PARTICLE ENGINE ===== */
document.addEventListener('DOMContentLoaded', () => {
    const canvas = document.getElementById('particle-canvas');
    const ctx = canvas.getContext('2d');
    let width, height, particles = [], strayParticles = [], time = 0;
    const cols = 80, rows = 60, spacing = 35;

    function initCanvas() {
        width = window.innerWidth;
        height = window.innerHeight * 1.2;
        canvas.width = width;
        canvas.height = height;
        createParticles();
    }

    function createParticles() {
        particles = []; strayParticles = [];
        const offsetX = width * 0.3, offsetY = height * 0.6;
        for (let i = 0; i < cols; i++) {
            for (let j = 0; j < rows; j++) {
                let x = (i - cols / 2) * spacing, z = (j - rows / 2) * spacing;
                particles.push({ gridX: x, gridZ: z, baseX: x + offsetX, baseY: offsetY, z: z, randomOffset: Math.random() * Math.PI * 2 });
            }
        }
        for (let k = 0; k < 150; k++) {
            strayParticles.push({ x: Math.random() * width, y: Math.random() * height, vx: (Math.random() - 0.5) * 0.5, vy: (Math.random() - 0.5) * 0.5, size: Math.random() * 1.5 + 0.5 });
        }
    }

    function animate() {
        ctx.clearRect(0, 0, width, height);
        time += 0.01;
        particles.forEach(p => {
            const distFromCenter = Math.sqrt(p.gridX * p.gridX * 0.5 + p.gridZ * p.gridZ * 0.2);
            const peakHeight = 400 * Math.exp(-(distFromCenter * distFromCenter) / 40000);
            const wave = Math.sin(p.gridX * 0.01 + time) * 30 + Math.cos(p.gridZ * 0.015 - time) * 30;
            p.y = p.baseY - peakHeight + wave;
            const perspective = 1200, renderZ = p.z + 800;
            const scale = perspective / (perspective + renderZ);
            const screenX = (width * 0.7) + p.gridX * scale, screenY = p.y * scale;
            let opacity = Math.min(1, Math.max(0, 1 - (renderZ - 400) / 1000));
            if (screenX > 0 && screenX < width && screenY > 0 && screenY < height && opacity > 0.05) {
                ctx.beginPath();
                ctx.arc(screenX, screenY, 1.2 * scale, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(26, 115, 232, ${opacity * 0.8})`;
                ctx.fill();
            }
        });
        strayParticles.forEach(sp => {
            sp.x += sp.vx; sp.y += sp.vy;
            if (sp.x < 0 || sp.x > width) sp.vx *= -1;
            if (sp.y < 0 || sp.y > height) sp.vy *= -1;
            ctx.beginPath();
            ctx.arc(sp.x, sp.y, sp.size, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(26, 115, 232, 0.4)`;
            ctx.fill();
        });
        requestAnimationFrame(animate);
    }

    window.addEventListener('resize', initCanvas);
    initCanvas();
    animate();
});

/* ===== PLATFORM TAB SWITCHER ===== */
function switchPlatform(platform, btn) {
    document.querySelectorAll('.platform-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.ptab-btn').forEach(el => el.classList.remove('active'));
    const target = document.getElementById('plat-' + platform);
    if (target) target.classList.add('active');
    btn.classList.add('active');
}
