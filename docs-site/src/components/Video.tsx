import useBaseUrl from '@docusaurus/useBaseUrl';
import React from 'react';

interface VideoProps extends React.VideoHTMLAttributes<HTMLVideoElement> {
  src: string;
}

export default function Video({src, ...props}: VideoProps) {
  return (
    <video {...props}>
      <source src={useBaseUrl(src)} type="video/mp4" />
    </video>
  );
}
