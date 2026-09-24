import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { ImButton } from "./ImButton";
import { ImSectionHeader } from "./ImSectionHeader";
import { ImSelect } from "./ImSelect";
import { ImSeparator } from "./ImSeparator";
import { ImToggle } from "./ImToggle";
import { MaterialIcon } from "./MaterialIcon";

/**
 * The brief once Monthly Highlights is picked, as memory_brief.py draws it:
 * the type's own parameters, then How long, the folded Advanced and the Cut
 * button. The brief scene and the refused-cut scene both show it.
 */
export const BriefParams: React.FC = () => (
  <>
    <div style={{ display: "flex", gap: 24, alignItems: "flex-end" }}>
      <ImSelect label="Year" value="2024" style={{ width: 160 }} />
      <ImSelect label="Month" value="June" style={{ width: 192 }} />
    </div>
    <ImSelect
      label="Only with (optional)"
      value=""
      style={{ width: 288, marginTop: 14 }}
    />
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        marginTop: 10,
        padding: "8px 0",
      }}
    >
      <MaterialIcon name="tune" size={20} color={COLORS.textSecondary} />
      <span style={{ fontSize: 14, color: COLORS.text, flex: 1 }}>
        Advanced people condition
      </span>
      <MaterialIcon
        name="keyboard_arrow_down"
        size={22}
        color={COLORS.textSecondary}
      />
    </div>
  </>
);

export const BriefFooter: React.FC<{ cutScale?: number }> = ({ cutScale = 1 }) => (
  <>
    <div style={{ marginTop: 16 }}>
      <ImSectionHeader icon="timer" title="How long" />
    </div>
    <ImToggle
      label="Auto duration"
      checked
      trailing={
        <>
          <span
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: COLORS.text,
              fontFamily,
              marginLeft: 8,
            }}
          >
            Auto &middot; 1m 00s
          </span>
          <span
            style={{
              fontSize: 11,
              color: COLORS.textSecondary,
              fontFamily,
              marginLeft: 8,
            }}
          >
            the type&rsquo;s default length, shorter if the media cannot fill it
          </span>
        </>
      }
    />

    {/* Advanced expansion — collapsed, as the brief opens */}
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        marginTop: 8,
        padding: "10px 0",
      }}
    >
      <MaterialIcon name="tune" size={20} color={COLORS.textSecondary} />
      <span style={{ fontSize: 14, color: COLORS.text, flex: 1 }}>
        Advanced
      </span>
      <MaterialIcon
        name="keyboard_arrow_down"
        size={22}
        color={COLORS.textSecondary}
      />
    </div>

    <ImSeparator />

    <ImButton
      text="Cut"
      variant="primary"
      icon="content_cut"
      fullWidth
      style={{
        padding: "12px 20px",
        transform: `scale(${cutScale})`,
      }}
    />
  </>
);
