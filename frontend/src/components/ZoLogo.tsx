import Image from "next/image";

type ZoLogoSize = "sidebar" | "compact" | "mark";

interface ZoLogoProps {
  collapsed?: boolean;
  size?: ZoLogoSize;
  className?: string;
}

const sizeConfig: Record<
  ZoLogoSize,
  { box: string; width: number; height: number; imageClass: string }
> = {
  sidebar: {
    box: "w-[132px] rounded-lg bg-white p-2",
    width: 116,
    height: 32,
    imageClass: "h-auto w-full object-contain",
  },
  compact: {
    box: "w-[108px] rounded-lg bg-white p-1.5",
    width: 96,
    height: 28,
    imageClass: "h-auto w-full object-contain",
  },
  mark: {
    box: "h-10 w-10 rounded-lg bg-white p-1.5",
    width: 28,
    height: 28,
    imageClass: "h-full w-full object-contain",
  },
};

export function ZoLogo({
  collapsed = false,
  size,
  className = "",
}: ZoLogoProps) {
  const resolvedSize = size ?? (collapsed ? "mark" : "sidebar");
  const config = sizeConfig[resolvedSize];

  return (
    <div
      className={`flex shrink-0 items-center ${className}`}
      aria-label="Zo Agency"
    >
      <div className={`flex items-center justify-center ${config.box}`}>
        <Image
          src="/zo-agency-logo.png"
          alt="Zo Agency"
          width={config.width}
          height={config.height}
          className={config.imageClass}
          priority
        />
      </div>
    </div>
  );
}
