import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

type StepStatus = "completed" | "active" | "upcoming";

type Props = {
  activeStep: number; // 1-4
  completedSteps?: number[];
};

const STEPS = [
  { icon: "⚙️", label: "Configuration" },
  { icon: "🎬", label: "Clip Review" },
  { icon: "🎛️", label: "Options" },
  { icon: "📥", label: "Export" },
];

export const Sidebar: React.FC<Props> = ({
  activeStep,
  completedSteps = [],
}) => {
  const getStatus = (step: number): StepStatus => {
    if (completedSteps.includes(step)) return "completed";
    if (step === activeStep) return "active";
    return "upcoming";
  };

  return (
    <div
      style={{
        width: 200,
        backgroundColor: COLORS.bg,
        borderRight: `1px solid ${COLORS.border}`,
        padding: "16px 0",
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        height: "100%",
      }}
    >
      {/* Branding */}
      <div
        style={{
          padding: "0 16px 16px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          borderBottom: `1px solid ${COLORS.border}`,
          marginBottom: 12,
        }}
      >
        <span style={{ fontSize: 20 }}>🎬</span>
        <span
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: COLORS.text,
            fontFamily,
          }}
        >
          Immich Memories
        </span>
      </div>

      {/* Steps */}
      {STEPS.map((step, i) => {
        const num = i + 1;
        const status = getStatus(num);
        const isActive = status === "active";
        const isCompleted = status === "completed";

        return (
          <div
            key={step.label}
            style={{
              padding: "10px 16px",
              display: "flex",
              alignItems: "center",
              gap: 10,
              backgroundColor: isActive
                ? "rgba(107, 143, 232, 0.1)"
                : "transparent",
              borderLeft: isActive
                ? `3px solid ${COLORS.primary}`
                : "3px solid transparent",
            }}
          >
            <span style={{ fontSize: 16 }}>
              {isCompleted ? "✅" : step.icon}
            </span>
            <span
              style={{
                fontSize: 13,
                fontFamily,
                fontWeight: isActive ? 600 : 400,
                color: isActive ? COLORS.text : COLORS.textSecondary,
              }}
            >
              {step.label}
            </span>
          </div>
        );
      })}
    </div>
  );
};
