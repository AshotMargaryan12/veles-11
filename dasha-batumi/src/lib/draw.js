// Рисунки для игры: хачапури по-аджарски, золотой хачапури, туча и тарелка.

export function drawKhachapuri(ctx, x, y, s, { golden = false, rot = 0, t = 0 } = {}) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);

  if (golden) {
    ctx.shadowColor = 'rgba(255, 207, 77, 0.9)';
    ctx.shadowBlur = s * 0.9;
  }

  // Лодочка из теста с закрученными кончиками
  const crust = ctx.createLinearGradient(0, -s * 0.6, 0, s * 0.6);
  crust.addColorStop(0, golden ? '#ffe28a' : '#eeb066');
  crust.addColorStop(1, golden ? '#e0a21c' : '#b9692a');
  ctx.fillStyle = crust;
  ctx.beginPath();
  ctx.moveTo(-s * 1.08, -s * 0.06);
  ctx.bezierCurveTo(-s * 0.7, -s * 0.78, s * 0.7, -s * 0.78, s * 1.08, s * 0.06);
  ctx.bezierCurveTo(s * 0.7, s * 0.74, -s * 0.7, s * 0.74, -s * 1.08, -s * 0.06);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.lineWidth = Math.max(1, s * 0.07);
  ctx.strokeStyle = golden ? '#b07a0c' : '#8a4a1c';
  ctx.stroke();

  // Сыр
  ctx.fillStyle = golden ? '#fff3b5' : '#ffe07a';
  ctx.beginPath();
  ctx.moveTo(-s * 0.72, 0);
  ctx.bezierCurveTo(-s * 0.45, -s * 0.46, s * 0.45, -s * 0.46, s * 0.72, 0);
  ctx.bezierCurveTo(s * 0.45, s * 0.44, -s * 0.45, s * 0.44, -s * 0.72, 0);
  ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.35)';
  ctx.beginPath();
  ctx.ellipse(-s * 0.35, -s * 0.12, s * 0.16, s * 0.07, -0.3, 0, Math.PI * 2);
  ctx.fill();

  // Желток
  const yolk = ctx.createRadialGradient(-s * 0.05, -s * 0.06, s * 0.02, 0, 0, s * 0.24);
  yolk.addColorStop(0, '#ffd54a');
  yolk.addColorStop(1, '#f39a12');
  ctx.fillStyle = yolk;
  ctx.beginPath();
  ctx.arc(0, 0, s * 0.22, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.7)';
  ctx.beginPath();
  ctx.ellipse(-s * 0.07, -s * 0.08, s * 0.06, s * 0.035, -0.5, 0, Math.PI * 2);
  ctx.fill();

  // Кусочек масла
  ctx.save();
  ctx.translate(s * 0.36, -s * 0.12);
  ctx.rotate(0.35);
  ctx.fillStyle = '#fff6c8';
  ctx.fillRect(-s * 0.09, -s * 0.09, s * 0.18, s * 0.18);
  ctx.restore();

  ctx.restore();

  if (golden) {
    // Искорки вокруг
    ctx.save();
    ctx.fillStyle = '#fff1b0';
    for (let i = 0; i < 3; i++) {
      const a = t * 2.2 + (i * Math.PI * 2) / 3;
      const r = s * 1.25;
      const px = x + Math.cos(a) * r;
      const py = y + Math.sin(a) * r * 0.7;
      const k = s * 0.13 * (0.6 + 0.4 * Math.sin(t * 6 + i));
      ctx.beginPath();
      ctx.moveTo(px, py - k * 2);
      ctx.lineTo(px + k * 0.5, py);
      ctx.lineTo(px, py + k * 2);
      ctx.lineTo(px - k * 0.5, py);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }
}

export function drawCloud(ctx, x, y, s, t = 0) {
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = '#7c90a8';
  ctx.beginPath();
  ctx.arc(-s * 0.5, s * 0.1, s * 0.42, 0, Math.PI * 2);
  ctx.arc(0, -s * 0.15, s * 0.55, 0, Math.PI * 2);
  ctx.arc(s * 0.55, s * 0.08, s * 0.4, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#a9bbcf';
  ctx.beginPath();
  ctx.arc(-s * 0.45, 0, s * 0.34, 0, Math.PI * 2);
  ctx.arc(0, -s * 0.24, s * 0.44, 0, Math.PI * 2);
  ctx.arc(s * 0.5, -s * 0.02, s * 0.3, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillRect(-s * 0.7, -s * 0.05, s * 1.35, s * 0.3);

  // Капли
  ctx.strokeStyle = '#8fc4f0';
  ctx.lineWidth = Math.max(1.5, s * 0.1);
  ctx.lineCap = 'round';
  ctx.beginPath();
  for (let i = 0; i < 4; i++) {
    const dx = -s * 0.5 + i * s * 0.34;
    const off = ((t * 1.6 + i * 0.37) % 1) * s * 0.45;
    ctx.moveTo(dx, s * 0.5 + off);
    ctx.lineTo(dx - s * 0.06, s * 0.68 + off);
  }
  ctx.stroke();
  ctx.restore();
}

export function drawPlate(ctx, x, y, w) {
  const h = w * 0.16;
  ctx.save();
  ctx.fillStyle = 'rgba(0,0,0,0.22)';
  ctx.beginPath();
  ctx.ellipse(x, y + h * 0.7, w * 0.48, h * 0.55, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#fbf6ec';
  ctx.beginPath();
  ctx.ellipse(x, y, w / 2, h, 0, 0, Math.PI * 2);
  ctx.fill();
  // синий ободок, как на грузинской керамике
  ctx.strokeStyle = '#2f6f9f';
  ctx.lineWidth = Math.max(2, w * 0.03);
  ctx.beginPath();
  ctx.ellipse(x, y, w / 2 - ctx.lineWidth, h - ctx.lineWidth * 0.6, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = '#efe6d6';
  ctx.beginPath();
  ctx.ellipse(x, y + h * 0.08, w * 0.3, h * 0.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}
