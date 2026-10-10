import { useState } from "react";
import { ChevronDown, FolderTree, Layers3, Shapes, Shield } from "lucide-react";
import type { LibraryResponse, Taxonomy } from "./model";

interface Props {
  taxonomy: Taxonomy | null;
  counts: LibraryResponse["counts"];
  mode: string;
  category: string;
  subtype: string;
  total: number;
  onDirectory: () => void;
  onCategory: (category: string, subtype?: string) => void;
}

export function LibraryMenu({
  taxonomy,
  counts,
  mode,
  category,
  subtype,
  total,
  onDirectory,
  onCategory,
}: Props) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const categories =
    taxonomy?.categories.filter((item) => item.id !== "adult") || [];
  const populated = categories.filter(
    (item) => (counts?.categories[item.id] || 0) > 0,
  );
  const other = categories.filter((item) => !populated.includes(item));
  const groups = [...new Set(other.map((item) => item.group))];
  const choose = (value: string, child = "") => {
    onCategory(value, child);
    setMobileOpen(false);
  };
  const categoryButton = (item: { id: string; label: string }) => (
    <button
      key={item.id}
      className={`category-link ${mode === "catalog" && category === item.id ? "active" : ""}`}
      onClick={() => choose(item.id)}
      aria-current={
        mode === "catalog" && category === item.id ? "true" : undefined
      }
    >
      <span>{item.label}</span>
      <span className="category-count">{counts?.categories[item.id] || 0}</span>
    </button>
  );
  return (
    <nav aria-label="资源库菜单" className="library-navigation">
      <div className="library-nav-heading">资源空间</div>
      <div className="library-nav-primary">
        <button
          className={`category-link ${mode === "catalog" && !category ? "active" : ""}`}
          onClick={() => choose("")}
        >
          <Layers3 size={18} />
          <span>全部资源</span>
          <span className="category-count">{total}</span>
        </button>
        <button
          className={`category-link ${mode === "directory" ? "active" : ""}`}
          onClick={() => {
            onDirectory();
            setMobileOpen(false);
          }}
        >
          <FolderTree size={18} />
          <span>真实目录</span>
        </button>
        <button
          className="category-link mobile-category-toggle"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((value) => !value)}
        >
          <Shapes size={18} />
          <span>分类</span>
          <ChevronDown size={15} />
        </button>
      </div>
      <div className={`category-tree ${mobileOpen ? "mobile-open" : ""}`}>
        <div className="category-section-label">
          内容分类<span>{categories.length}</span>
        </div>
        {populated.map(categoryButton)}
        <details
          className="all-categories"
          open={other.some((item) => item.id === category) || undefined}
        >
          <summary>
            <Shapes size={16} />
            <span>全部分类</span>
            <ChevronDown size={15} />
          </summary>
          {groups.map((group) => (
            <div key={group}>
              <h3>{group}</h3>
              {other.filter((item) => item.group === group).map(categoryButton)}
            </div>
          ))}
        </details>
        <div className="adult-section">
          <div className="category-section-label">
            <Shield size={14} />
            NSFW 独立分区
          </div>
          <button
            className={`category-link ${mode === "catalog" && category === "adult" && !subtype ? "active" : ""}`}
            onClick={() => {
              onCategory("adult");
              setMobileOpen(true);
            }}
          >
            <span>成人内容</span>
            <span className="category-count">
              {counts?.categories.adult || 0}
            </span>
          </button>
          {category === "adult" && mode === "catalog" && (
            <div className="adult-subtypes">
              {taxonomy?.adult_subtypes.map((child) => (
                <button
                  key={child.id}
                  className={`category-link ${subtype === child.id ? "active" : ""}`}
                  onClick={() => choose("adult", child.id)}
                >
                  <span>{child.label}</span>
                  <span className="category-count">
                    {counts?.subtypes[child.id] || 0}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
        <p className="nav-footnote">分类视图不改变云盘中的真实目录。</p>
      </div>
    </nav>
  );
}
