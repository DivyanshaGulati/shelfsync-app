import ProductImage from './ProductImage.jsx'

export default function ProductIdentity({ name, sku, unit, category, thumbnail }) {
  return <div className="product-identity">
    <ProductImage sku={sku} name={name} category={category} thumbnail={thumbnail} />
    <div>
      <strong>{name}</strong>
      <small>{sku}{unit && ` · ${unit}`}</small>
    </div>
  </div>
}
