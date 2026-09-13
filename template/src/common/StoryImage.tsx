import React from 'react';
import {Img, interpolate, staticFile} from 'remotion';

type StoryImageProps = {
  /** Path relative to public/, for example assets/my-film/article/005.png. */
  src: string;
  x: number;
  y: number;
  w: number;
  h: number;
  N?: number;
  fit?: 'contain' | 'cover';
  position?: string;
  radius?: number;
  borderColor?: string;
  background?: string;
  glow?: boolean;
  opacity?: number;
};

/**
 * Render an approved local source image inside the MG visual system.
 * Attribution is kept in the source manifest and delivery notes; this
 * component does not add or remove watermarks or render a source badge.
 */
export const StoryImage: React.FC<StoryImageProps> = ({
  src,
  x,
  y,
  w,
  h,
  N,
  fit = 'contain',
  position = 'center',
  radius = 22,
  borderColor = '#FFFFFF',
  background = '#060606',
  glow = true,
  opacity = 1,
}) => {
  if (/^(?:https?:)?\/\//i.test(src)) {
    throw new Error('StoryImage only accepts local paths relative to public/');
  }
  const localSrc = src.replace(/^\/+/, '');
  return (
    <>
      {glow ? (
        <div
          style={{
            position: 'absolute',
            left: x,
            top: y,
            width: w,
            height: h,
            borderRadius: radius,
            boxShadow: `0 0 22px 6px rgba(20,184,166,${N === undefined ? 0.34 : interpolate(Math.sin(N / 15), [-1, 1], [0.22, 0.42])}), 0 0 64px 18px rgba(20,184,166,.18)`,
          }}
        />
      ) : null}
      <div
        style={{
          position: 'absolute',
          left: x,
          top: y,
          width: w,
          height: h,
          borderRadius: radius,
          overflow: 'hidden',
          border: `3px solid ${borderColor}`,
          boxSizing: 'border-box',
          background,
          opacity,
        }}
      >
        <Img
          src={staticFile(localSrc)}
          style={{
            width: '100%',
            height: '100%',
            objectFit: fit,
            objectPosition: position,
            background,
          }}
        />
      </div>
    </>
  );
};
