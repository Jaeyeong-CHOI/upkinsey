import React from "react";
/* Persona portrait SVG generator
   Stylized silhouette per persona — distinguishable but abstract.
   Each persona gets: hair shape (by age + gender), accessory (glasses/scarf/hat),
   subtle color tint matching their stance.
*/

const { useMemo: _useMemoPortrait } = React;

// Portrait config per persona id
// face: oval; hair: short/grey/bob/etc; accessory: optional
const PORTRAIT_CFG = {
  // 조성운 58 male — short grey hair, weathered
  p1: { gender: "m", age: "old",   hair: "shortGrey",  accessory: null,        skin: "#d4b89a" },
  // 엄세욱 55 male — close-cropped greying
  p2: { gender: "m", age: "old",   hair: "buzzGrey",   accessory: null,        skin: "#cfa886" },
  // 임재영 20 male — modern fringe
  p3: { gender: "m", age: "young", hair: "fringe",     accessory: null,        skin: "#e2c4a3" },
  // 박진희 66 female — short bob, glasses
  p4: { gender: "f", age: "old",   hair: "bobShort",   accessory: "glasses",   skin: "#e7c4a8" },
  // 조성렬 39 male — middle-aged neat side part
  p5: { gender: "m", age: "mid",   hair: "sidePart",   accessory: null,        skin: "#d8b495" },
  // 이용준 42 male — short businessman
  p6: { gender: "m", age: "mid",   hair: "shortBlack", accessory: null,        skin: "#dbb290" },
  // 조귀순 66 female — perm, older
  p7: { gender: "f", age: "old",   hair: "perm",       accessory: null,        skin: "#e8c8ad" },
  // 남희 52 female — shoulder bob
  p8: { gender: "f", age: "mid",   hair: "bobMid",     accessory: null,        skin: "#dfbf9f" }
};

const HAIR_COLORS = {
  shortGrey:  "#8a8a92",
  buzzGrey:   "#6f6f78",
  fringe:     "#1f1f24",
  bobShort:   "#9a9098",
  sidePart:   "#2a2620",
  shortBlack: "#1f1c1a",
  perm:       "#a89992",
  bobMid:     "#2c2622"
};

function PersonaPortrait({ id, size = 64, ring = null }) {
  const cfg = PORTRAIT_CFG[id] || PORTRAIT_CFG.p1;
  const hairColor = HAIR_COLORS[cfg.hair] || "#333";
  const skin = cfg.skin;
  const stroke = "rgba(0,0,0,0.25)";

  // hair paths
  let hair = null;
  switch (cfg.hair) {
    case "shortGrey":
      hair = <path d="M 18 38 Q 18 18 50 18 Q 82 18 82 38 Q 82 30 76 26 Q 70 22 58 22 Q 42 22 32 26 Q 22 30 18 38 Z" fill={hairColor} />;
      break;
    case "buzzGrey":
      hair = <path d="M 22 38 Q 24 22 50 22 Q 76 22 78 38 Q 78 28 70 26 Q 60 24 50 24 Q 38 24 30 26 Q 24 28 22 38 Z" fill={hairColor} />;
      break;
    case "fringe":
      hair = <path d="M 16 42 Q 14 14 50 14 Q 86 14 84 42 Q 80 32 72 30 Q 68 36 60 36 Q 56 28 48 32 Q 42 36 36 32 Q 28 30 24 36 Q 20 38 16 42 Z" fill={hairColor} />;
      break;
    case "bobShort":
      hair = <path d="M 14 46 Q 14 14 50 14 Q 86 14 86 46 L 84 56 L 78 50 Q 78 28 50 28 Q 22 28 22 50 L 16 56 Z" fill={hairColor} />;
      break;
    case "sidePart":
      hair = <path d="M 18 40 Q 18 16 50 16 Q 82 16 82 40 Q 80 26 64 22 Q 56 22 50 28 Q 38 22 26 28 Q 20 32 18 40 Z" fill={hairColor} />;
      break;
    case "shortBlack":
      hair = <path d="M 20 38 Q 20 18 50 18 Q 80 18 80 38 Q 78 26 66 22 Q 50 20 36 24 Q 24 28 20 38 Z" fill={hairColor} />;
      break;
    case "perm":
      hair = (
        <g fill={hairColor}>
          <path d="M 14 44 Q 14 14 50 14 Q 86 14 86 44 Q 86 38 80 38 Q 80 30 72 28 Q 72 22 62 22 Q 62 18 50 18 Q 38 18 38 22 Q 28 22 28 28 Q 20 30 20 38 Q 14 38 14 44 Z" />
          <circle cx="22" cy="46" r="6" />
          <circle cx="78" cy="46" r="6" />
        </g>
      );
      break;
    case "bobMid":
      hair = <path d="M 14 50 Q 14 14 50 14 Q 86 14 86 50 L 86 62 L 80 56 Q 80 30 50 30 Q 20 30 20 56 L 14 62 Z" fill={hairColor} />;
      break;
    default:
      hair = null;
  }

  const accessory = cfg.accessory === "glasses" ? (
    <g stroke="#2a2a2e" strokeWidth="1.6" fill="none">
      <circle cx="38" cy="48" r="6.5" />
      <circle cx="62" cy="48" r="6.5" />
      <line x1="44.5" y1="48" x2="55.5" y2="48" />
    </g>
  ) : null;

  return (
    <svg viewBox="0 0 100 100" width={size} height={size} style={{ display: "block", overflow: "visible" }}>
      <defs>
        <clipPath id={`pp-clip-${id}`}>
          <circle cx="50" cy="50" r="48" />
        </clipPath>
        <linearGradient id={`pp-bg-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stopColor="var(--surface-3)" />
          <stop offset="100%" stopColor="var(--surface)" />
        </linearGradient>
      </defs>

      {/* Background disc */}
      <circle cx="50" cy="50" r="48" fill={`url(#pp-bg-${id})`} stroke="var(--border-strong)" strokeWidth="1" />

      <g clipPath={`url(#pp-clip-${id})`}>
        {/* Shoulders / body */}
        <path d={`M 6 100 Q 6 76 30 70 Q 50 82 70 70 Q 94 76 94 100 Z`} fill={hairColor === "#1f1f24" || hairColor === "#1f1c1a" ? "#2a2628" : "#3a3438"} opacity="0.7" />
        <path d={`M 6 100 Q 6 76 30 70 Q 50 82 70 70 Q 94 76 94 100 Z`} fill={"rgba(255,255,255,0.04)"} />

        {/* Neck */}
        <rect x="44" y="62" width="12" height="14" fill={skin} opacity="0.85" />

        {/* Face */}
        <ellipse cx="50" cy="50" rx="22" ry="26" fill={skin} stroke={stroke} strokeWidth="0.5" />

        {/* Hair */}
        {hair}

        {/* Eyes */}
        <ellipse cx="40" cy="50" rx="1.4" ry="1.8" fill="#2a2622" />
        <ellipse cx="60" cy="50" rx="1.4" ry="1.8" fill="#2a2622" />

        {/* Mouth */}
        <path d="M 45 60 Q 50 62 55 60" stroke="#5a3a30" strokeWidth="1" fill="none" strokeLinecap="round" />

        {/* Accessory */}
        {accessory}
      </g>

      {/* Ring overlay */}
      {ring && (
        <circle cx="50" cy="50" r="48" fill="none" stroke={ring} strokeWidth="2.5" />
      )}
    </svg>
  );
}


export { PersonaPortrait };
