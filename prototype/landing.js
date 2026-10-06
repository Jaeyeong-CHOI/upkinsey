import { DEMO_PERSONAS, DEMO_THOUGHTS } from "./data.js";
import "./landing-base.css";
import "./landing.css";

/* 업킨지 앤 컴퍼니 — 랜딩 인터랙티브 (가벼운 hero 캔버스 + 스크롤 리빌) */

(function() {
  /* ===== Hero canvas — converging persona nodes (subtle) ===== */
  const hero = document.getElementById('hero-canvas');
  if (hero) {
    const ctx = hero.getContext('2d');
    let W, H, dpr = Math.min(window.devicePixelRatio || 1, 2);
    const personas = DEMO_PERSONAS || [];

    function resize() {
      const rect = hero.getBoundingClientRect();
      W = rect.width; H = rect.height;
      hero.width = W * dpr; hero.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener('resize', resize);

    let t = 0;
    const nodeCount = 8;
    const nodes = Array.from({ length: nodeCount }, (_, i) => {
      const a = (i / nodeCount) * Math.PI * 2 - Math.PI / 2;
      return {
        a, baseR: 160, drift: Math.random() * 100,
        color: ["pos", "pos", "pos", "neu", "neu", "neg", "neg", "neg"][i],
        persona: personas[i]
      };
    });

    const colorMap = {
      pos: 'rgba(78, 216, 163, ',
      neu: 'rgba(229, 176, 78, ',
      neg: 'rgba(240, 106, 106, '
    };

    function loop() {
      t++;
      const cx = W * 0.5;
      const cy = H * 0.5;

      ctx.clearRect(0, 0, W, H);

      // Faint grid
      ctx.strokeStyle = 'rgba(255,255,255,0.025)';
      ctx.lineWidth = 1;
      for (let i = 0; i < W; i += 40) {
        ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, H); ctx.stroke();
      }
      for (let i = 0; i < H; i += 40) {
        ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(W, i); ctx.stroke();
      }

      // Center dot
      ctx.fillStyle = 'rgba(132, 120, 232, 0.9)';
      ctx.beginPath(); ctx.arc(cx, cy, 8, 0, Math.PI * 2); ctx.fill();

      // Outer dashed ring
      ctx.strokeStyle = 'rgba(107, 95, 219, 0.2)';
      ctx.setLineDash([3, 7]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, 190, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);

      // Persona nodes
      nodes.forEach((n, i) => {
        const wobble = Math.sin(t / 80 + n.drift) * 10;
        const r = n.baseR + wobble;
        const a = n.a + (t / 1800);
        const x = cx + Math.cos(a) * r;
        const y = cy + Math.sin(a) * r * 0.62;

        // Line to center
        ctx.strokeStyle = colorMap[n.color] + '0.16)';
        ctx.lineWidth = 0.7;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(x, y);
        ctx.stroke();

        // Node body
        ctx.fillStyle = colorMap[n.color] + '0.9)';
        ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2); ctx.fill();

        // Inner ring
        ctx.strokeStyle = 'rgba(255,255,255,0.3)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.stroke();

        // Name tag
        if (n.persona) {
          ctx.fillStyle = 'rgba(236, 236, 240, 0.65)';
          ctx.font = '11.5px "Pretendard Variable", Pretendard, system-ui';
          ctx.textAlign = (Math.cos(a) > 0) ? 'left' : 'right';
          ctx.textBaseline = 'middle';
          const off = (Math.cos(a) > 0) ? 14 : -14;
          ctx.fillText(n.persona.name + ' · ' + n.persona.age + '세', x + off, y);
        }
      });

      requestAnimationFrame(loop);
    }
    loop();
  }

  /* ===== Hero thought rail ===== */
  const rail = document.getElementById('thought-rail');
  if (rail) {
    const thoughts = (DEMO_THOUGHTS) || [
      "응답자 패널을 생성하는 중…",
      "응답을 모으는 중…",
      "결과를 정리하는 중…"
    ];
    let idx = 0;
    rail.textContent = thoughts[0];
    rail.style.transition = 'opacity 0.3s ease';
    setInterval(() => {
      idx = (idx + 1) % thoughts.length;
      rail.style.opacity = '0';
      setTimeout(() => {
        rail.textContent = thoughts[idx];
        rail.style.opacity = '1';
      }, 200);
    }, 2400);
  }

  /* ===== Scroll reveal ===== */
  const reveal = document.querySelectorAll('.lp-section, .lp-strip');
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        e.target.classList.add('in');
        io.unobserve(e.target);
      }
    });
  }, { threshold: 0.12 });
  reveal.forEach(el => io.observe(el));
})();
