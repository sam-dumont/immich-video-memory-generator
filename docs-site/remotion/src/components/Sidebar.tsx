import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

type StepStatus = "completed" | "active" | "upcoming";

type Props = {
  activeStep: number; // 1-4
  completedSteps?: number[];
};

const NAV_STEPS = [
  { icon: "settings", label: "Configuration" },
  { icon: "video_library", label: "Clip Review" },
  { icon: "tune", label: "Options" },
  { icon: "download", label: "Export" },
];

const BOTTOM_NAV = [
  { icon: "description", label: "Config" },
  { icon: "cached", label: "Cache" },
];

const THEME_ICONS = ["light_mode", "brightness_auto", "dark_mode"];

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
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        height: "100%",
      }}
    >
      {/* Branding */}
      <div
        style={{
          padding: "14px 16px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          borderBottom: `1px solid ${COLORS.border}`,
        }}
      >
        <MaterialIcon name="movie" size={22} color={COLORS.primary} />
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

      {/* Main nav steps */}
      <div style={{ padding: "8px 0" }}>
        {NAV_STEPS.map((step, i) => {
          const num = i + 1;
          const status = getStatus(num);
          const isActive = status === "active";
          const isCompleted = status === "completed";

          return (
            <div
              key={step.label}
              style={{
                padding: "9px 14px",
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
              <MaterialIcon
                name={step.icon}
                size={20}
                color={
                  isActive
                    ? COLORS.primary
                    : isCompleted
                      ? COLORS.textSecondary
                      : COLORS.textSecondary
                }
              />
              <span
                style={{
                  fontSize: 13,
                  fontFamily,
                  fontWeight: isActive ? 600 : 400,
                  color: isActive ? COLORS.primary : COLORS.textSecondary,
                  flex: 1,
                }}
              >
                {step.label}
              </span>
              {isCompleted && (
                <MaterialIcon
                  name="check_circle"
                  size={16}
                  color={COLORS.success}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Bottom nav separator */}
      <div
        style={{
          height: 1,
          backgroundColor: COLORS.border,
          margin: "0 14px",
        }}
      />

      {/* Bottom nav items */}
      <div style={{ padding: "8px 0" }}>
        {BOTTOM_NAV.map((item) => (
          <div
            key={item.label}
            style={{
              padding: "9px 14px",
              display: "flex",
              alignItems: "center",
              gap: 10,
              borderLeft: "3px solid transparent",
            }}
          >
            <MaterialIcon
              name={item.icon}
              size={20}
              color={COLORS.textSecondary}
            />
            <span
              style={{
                fontSize: 13,
                fontFamily,
                fontWeight: 400,
                color: COLORS.textSecondary,
              }}
            >
              {item.label}
            </span>
          </div>
        ))}
      </div>

      {/* Theme toggle buttons */}
      <div
        style={{
          padding: "8px 14px 12px",
          display: "flex",
          gap: 4,
          borderTop: `1px solid ${COLORS.border}`,
        }}
      >
        {THEME_ICONS.map((icon, i) => {
          const isSelected = i === 2; // dark_mode selected
          return (
            <div
              key={icon}
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: "6px 0",
                borderRadius: 6,
                backgroundColor: isSelected
                  ? "rgba(107, 143, 232, 0.15)"
                  : "transparent",
              }}
            >
              <MaterialIcon
                name={icon}
                size={18}
                color={
                  isSelected ? COLORS.primary : COLORS.textSecondary
                }
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};
