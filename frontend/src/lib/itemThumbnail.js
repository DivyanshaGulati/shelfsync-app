const photoTypes = new Set(['image/jpeg', 'image/png', 'image/webp'])

export async function makeItemThumbnail(file) {
  if (!photoTypes.has(file.type)) throw new Error('Choose a JPG, PNG, or WebP photo.')
  if (file.size > 10 * 1024 * 1024) throw new Error('Choose a photo under 10 MB.')

  const image = await createImageBitmap(file).catch(() => {
    throw new Error('This photo could not be opened. Choose another image.')
  })
  try {
    const size = 112
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const context = canvas.getContext('2d')
    if (!context) throw new Error('The photo could not be resized.')
    const scale = Math.min(size / image.width, size / image.height)
    const width = Math.max(1, Math.round(image.width * scale))
    const height = Math.max(1, Math.round(image.height * scale))
    context.fillStyle = '#fff'
    context.fillRect(0, 0, size, size)
    context.drawImage(image, (size - width) / 2, (size - height) / 2, width, height)
    for (const quality of [0.8, 0.6, 0.4]) {
      const thumbnail = canvas.toDataURL('image/jpeg', quality)
      if (thumbnail.startsWith('data:image/jpeg;base64,') && thumbnail.length <= 12000) return thumbnail
    }
    throw new Error('The photo could not be resized. Choose another image.')
  } finally {
    image.close()
  }
}
