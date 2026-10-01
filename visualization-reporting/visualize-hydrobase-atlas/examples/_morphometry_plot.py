"""Paper-style, three-panel maps of existing HydroBase morphometry."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize, BoundaryNorm
from matplotlib.cm import ScalarMappable
from matplotlib.ticker import MaxNLocator
from rasterio.features import geometry_mask
from affine import Affine
from pyproj import CRS
from _atlas_common import AtlasError, extent_from_meta, save_figure
from _morphometry_maps import decorate_map


def render_morphometry(style,text,status,subbasins,meta,reaches,sub_table,output_dir,
                       language,source_results,warnings,basin):
    settings=json.loads((Path(__file__).resolve().parents[1]/"assets/morphometry-style-v2.json").read_text(encoding="utf-8"))
    crs=CRS.from_user_input(meta["crs"])
    if not crs.is_projected or any(abs(axis.unit_conversion_factor-1)>1e-9 for axis in crs.axis_info[:2]):
        raise AtlasError("形态三联图比例尺要求米制投影坐标系")
    if not basin.geometry.geom_type.isin(["Polygon","MultiPolygon"]).all():
        raise AtlasError("流域边界必须为面几何")
    xmin,ymin,xmax,ymax=basin.total_bounds;dx=xmax-xmin;dy=ymax-ymin
    if dx<=0 or dy<=0:raise AtlasError("流域边界范围无效")
    extent=[xmin-.07*dx,xmax+.07*dx,ymin-.07*dy,ymax+.07*dy]
    height=float(np.clip(3.42/(dx/dy)+1.0,*settings["height_range_inches"]))
    # Round to whole pixels for reproducible, accurately recorded output sizes.
    height=round(height*200)/200
    union=basin.geometry.union_all()
    within=geometry_mask([union.__geo_interface__],out_shape=subbasins.shape,
                         transform=Affine(*meta["transform"][:6]),invert=True)
    grid=np.full(subbasins.shape,np.nan)
    for row in sub_table.itertuples():grid[(subbasins==int(row.sub_id)) & within]=float(row.area_km2)
    valid=grid[np.isfinite(grid)]
    if not len(valid):raise AtlasError("无法映射子流域面积")
    display=reaches.copy();display.geometry=display.geometry.intersection(union)
    display=display[~display.geometry.is_empty]
    slopes=pd.to_numeric(display["slope_percent"],errors="coerce")
    levels=pd.to_numeric(display["topo_level"],errors="coerce")
    finite_slopes=slopes[np.isfinite(slopes)]
    finite_levels=levels[np.isfinite(levels)]
    if finite_slopes.empty or finite_levels.empty:raise AtlasError("河段坡度或拓扑层级缺少有效值")
    slope_max=max(float(finite_slopes.quantile(.98)),.01)
    level_max=max(int(finite_levels.max()),1)
    slope_map=LinearSegmentedColormap.from_list("hydrobase_slope",settings["slope_colors"])
    level_map=plt.get_cmap(settings["level_palette"]).resampled(level_max)
    level_norm=BoundaryNorm(np.arange(.5,level_max+1.5),level_max)
    with plt.rc_context({"font.family":settings["font_family"],"font.size":14,
                         "axes.unicode_minus":False,"text.color":"black","axes.labelcolor":"black"}):
        figure=plt.figure(figsize=(12,height),dpi=200,facecolor="white")
        figure.hydrotune_template_version=settings["template_version"]
        axes=[figure.add_axes([.055+i*.32,.30,.285,.66],facecolor="white") for i in range(3)]
        area_image=axes[0].imshow(grid,extent=extent_from_meta(meta),origin="upper",interpolation="nearest",
            cmap=settings["area_palette"],vmin=float(valid.min()),vmax=float(valid.max()) if valid.max()>valid.min() else float(valid.min()+1),zorder=1)
        display.assign(_value=slopes).plot(ax=axes[1],column="_value",cmap=slope_map,
            vmin=0,vmax=slope_max,linewidth=2.1,zorder=4)
        display.assign(_value=levels).plot(ax=axes[2],column="_value",cmap=level_map,
            norm=level_norm,linewidth=2.1,zorder=4)
        maps=[area_image,ScalarMappable(norm=Normalize(0,slope_max),cmap=slope_map),ScalarMappable(norm=level_norm,cmap=level_map)]
        for i,(axis,title,mappable) in enumerate(zip(axes,[text["area"],text["slope"],text["level"]],maps)):
            basin.boundary.plot(ax=axis,color="black",linewidth=1.15,zorder=12)
            decorate_map(axis,extent,{"colors":{"ink":"black"}},meta["crs"],language)
            figure.text(.1975+i*.32,.19,f"({chr(97+i)}) {title}",ha="center",va="center",fontsize=settings["caption_size"])
            cax=figure.add_axes([.09+i*.32,.095,.22,.035])
            bar=figure.colorbar(mappable,cax=cax,orientation="horizontal",
                extend="max" if i==1 and finite_slopes.max()>slope_max else "neither")
            bar.ax.tick_params(labelsize=settings["colorbar_size"],direction="in",length=3)
            bar.outline.set_edgecolor("black");bar.outline.set_linewidth(.8)
            bar.locator=MaxNLocator(nbins=3,integer=i==2);bar.update_ticks()
            if i==2:
                bar.set_ticks(list(range(1,level_max+1)) if level_max<=8 else sorted(set([1,level_max]+[int(x) for x in bar.get_ticks() if 1<=x<=level_max])))
        return save_figure(figure,output_dir,"hydrobase-morphometry",language,text["morph_title"],source_results,
            [{"id":"subbasin-area","source":"build_result.artifacts.subbasins_table","display_transform":f"existing area_km2 mapped by sub_id; vmin={valid.min():.6g}; vmax={valid.max():.6g}; display clipped to real boundary","palette":settings["area_palette"]},
             {"id":"reach-slope","source":"build_result.artifacts.reaches_table","display_transform":f"linear existing slope; 0 to P98={slope_max:.6g}; over-range indicated by colorbar extension","palette":"deep-blue-to-deep-red"},
             {"id":"topological-level","source":"build_result.artifacts.reaches_table","display_transform":f"discrete existing topo_level 1..{level_max}; not Strahler order","palette":settings["level_palette"]},
             {"id":"basin-boundary","source":"build_result.inputs.basin_boundary","display_transform":"real boundary overlaid on all three maps; WGS84 graticules projected to map CRS; metric scale; north arrow","palette":"black"}],
            meta["crs"],extent,warnings,status=="success")
