/* Local image preparation only. The preview is exactly what is sent after confirmation. */
export const PHOTO_BYTES = 1024 * 1024;
const readURL = blob => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result);
  reader.onerror = () => reject(new Error("invalid_photo"));
  reader.readAsDataURL(blob);
});

export async function preparePhoto(file) {
  if (!file || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("invalid_photo");
  if (file.size > 20 * 1024 * 1024) throw new Error("photo_too_large");
  const url = URL.createObjectURL(file), image = new Image();
  try {
    await new Promise((resolve, reject) => { image.onload=resolve; image.onerror=()=>reject(new Error("invalid_photo")); image.src=url; });
    if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth * image.naturalHeight > 30_000_000) throw new Error("photo_too_large");
    const canvas=document.createElement("canvas"), scale=Math.min(1,2560/Math.max(image.naturalWidth,image.naturalHeight));
    canvas.width=Math.max(1,Math.round(image.naturalWidth*scale)); canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
    const ctx=canvas.getContext("2d"); ctx.fillStyle="#fff"; ctx.fillRect(0,0,canvas.width,canvas.height); ctx.drawImage(image,0,0,canvas.width,canvas.height);
    // Re-encoding removes location/EXIF metadata and keeps the WS payload below HA's limit.
    for (const quality of [0.92,0.84,0.76]) {
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",quality));
      if (blob && blob.size<=PHOTO_BYTES) return {data:(await readURL(blob)).split(",")[1],mime_type:"image/jpeg"};
    }
    throw new Error("photo_too_large");
  } finally { URL.revokeObjectURL(url); }
}

export function photoPreview(photo, alt) {
  const image=document.createElement("img"); image.alt=alt;
  image.src=`data:${photo.mime_type};base64,${photo.data}`;
  image.className="meter-photo"; return image;
}
