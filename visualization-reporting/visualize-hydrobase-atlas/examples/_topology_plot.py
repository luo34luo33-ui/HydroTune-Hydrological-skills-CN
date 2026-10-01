"""Compact HydroBase topology map with geographic decorations."""
from pathlib import Path
import json
import textwrap
from collections import Counter
from matplotlib.offsetbox import TextArea, VPacker, AnchoredOffsetbox
import numpy as np
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import Normalize
from affine import Affine
from rasterio.features import shapes, geometry_mask
from shapely.geometry import shape, box
from pyproj import CRS
from _atlas_common import AtlasError, extent_from_meta, linear_cmap, save_figure
from _morphometry_maps import decorate_map


def render_topology(style,text,status,subbasins,meta,reaches,output_dir,language,
                    source_results,warnings,basin,sub_table,validation=None):
    # Reuse the established reach directions/outlet flags without scientific recomputation.
    from render_hydrobase_atlas import _plot_subbasins, _plot_reaches
    settings=json.loads((Path(__file__).resolve().parents[1]/"assets/topology-style-v2.json").read_text(encoding="utf-8"))
    crs=CRS.from_user_input(meta["crs"])
    if not crs.is_projected or any(abs(axis.unit_conversion_factor-1)>1e-9 for axis in crs.axis_info[:2]):
        raise AtlasError("拓扑图比例尺要求米制投影坐标系")
    if not basin.geometry.geom_type.isin(["Polygon","MultiPolygon"]).all():raise AtlasError("流域边界必须为面")
    union=basin.geometry.union_all();xmin,ymin,xmax,ymax=basin.total_bounds;dx=xmax-xmin;dy=ymax-ymin
    extent=[xmin-.08*dx,xmax+.08*dx,ymin-.08*dy,ymax+.08*dy]
    if dx<=0 or dy<=0:raise AtlasError("流域边界范围无效")
    # Only margins needed by the large coordinate labels, while preserving map aspect.
    map_width=9.7;map_height=map_width*dy/dx
    width=map_width+.8;height=map_height+.65
    factor=12/max(width,height);width=round(width*factor*200)/200;height=round(height*factor*200)/200
    with plt.rc_context({"font.family":settings["font_family"],"font.size":16,"text.color":"black","axes.unicode_minus":False}):
        figure=plt.figure(figsize=(width,height),dpi=200,facecolor="white")
        figure.hydrotune_template_version=settings["template_version"] if validation is None else "hydrotune.hydrobase-qc-dashboard.v2"
        if validation is None:figure.hydrotune_filename=text["topology_title"]
        axis=figure.add_axes([.65/(map_width+.8),.5/(map_height+.65),map_width/(map_width+.8),map_height/(map_height+.65)],facecolor="white")
        transform=Affine(*meta["transform"][:6])
        valid=np.isfinite(subbasins)&(subbasins>0)
        mask=geometry_mask([union.__geo_interface__],out_shape=subbasins.shape,transform=transform,invert=True)
        display=np.where(valid & mask,subbasins,np.nan)
        _plot_subbasins(axis,display,extent_from_meta(meta),style)
        polygons=[shape(geometry).intersection(union) for geometry,value in shapes(np.where(valid,subbasins,0).astype("int32"),mask=valid,transform=transform)]
        gpd.GeoSeries(polygons,crs=basin.crs).boundary.plot(ax=axis,color=settings["subbasin_edge"],linewidth=settings["subbasin_edge_width"],zorder=3)
        clipped=reaches.copy();clipped.geometry=clipped.geometry.intersection(union);clipped=clipped[~clipped.geometry.is_empty]
        max_order,_=_plot_reaches(axis,clipped,style,arrows=validation is None,labels=validation is None,text=text,outlet_labels=validation is None)
        for label in axis.texts:
            if label.get_text():label.set_fontsize(12)
        basin.boundary.plot(ax=axis,color="black",linewidth=settings["basin_edge_width"],zorder=12)
        decorate_map(axis,extent,{"colors":{"ink":"black"}},meta["crs"],language)
        axis.tick_params(labelsize=settings["tick_size"],direction="in",colors="black")
        cmap=linear_cmap(style,"water");norm=Normalize(1,max_order if max_order>1 else 2)
        handles=[Patch(facecolor=style["colors"]["subbasins"][0],edgecolor=settings["subbasin_edge"],label=text["subbasins"]),
                 Line2D([0],[0],color="black",lw=1.5,label="流域边界" if language=="zh" else "Basin boundary")]
        handles += [Line2D([0],[0],color=cmap(norm(order)),lw=style["line_widths"]["stream_base"]+order*style["line_widths"]["stream_step"],label=f"{text['order']} {order}") for order in range(1,max_order+1)]
        handles.append(Line2D([0],[0],marker="*",color="none",markerfacecolor="#F2C14E",markeredgecolor="black",markersize=12,label=text["outlet"]))
        def occupancy(x0,y0,x1,y1):
            rect=box(extent[0]+x0*(extent[1]-extent[0]),extent[2]+y0*(extent[3]-extent[2]),extent[0]+x1*(extent[1]-extent[0]),extent[2]+y1*(extent[3]-extent[2]))
            return union.intersection(rect).area/rect.area
        # Avoid the north arrow and lower-left scale; choose the quieter left-top or right-bottom corner.
        loc=min([("upper left",occupancy(.02,.57,.32,.98)),("lower right",occupancy(.68,.02,.98,.43))],key=lambda item:item[1])[0]
        if validation is None:
            legend=axis.legend(handles=handles,loc=loc,fontsize=settings["legend_size"],frameon=True,facecolor="white",edgecolor="none",framealpha=.88,labelspacing=.55)
            legend.set_zorder(60)
            count=int(sub_table["sub_id"].nunique())
            count_label=f"子流域数量：{count}" if language=="zh" else f"Subbasins: {count}"
            count_x,count_y,ha=(.96,.05,"right") if loc=="upper left" else (.04,.94,"left")
            axis.text(count_x,count_y,count_label,transform=axis.transAxes,ha=ha,va="bottom",fontsize=settings["count_size"],zorder=60,bbox={"facecolor":"white","edgecolor":"none","alpha":.88,"pad":4},gid="subbasin-count")
        else:
            counts=Counter(check.get("status") for check in validation.get("checks",[]))
            rows=[TextArea(text["checks"],textprops={"fontsize":18,"fontweight":"bold"})]
            if status=="error":rows.append(TextArea("QC FAILED",textprops={"fontsize":18,"color":style["colors"]["fail"],"fontweight":"bold"}))
            elif status=="warning":rows.append(TextArea("QC WARNING",textprops={"fontsize":16,"color":style["colors"]["warn"]}))
            rows += [TextArea(f"{key}   {counts.get(key,0)}",textprops={"fontsize":18,"color":style["colors"][color]}) for key,color in [("PASS","pass"),("WARN","warn"),("FAIL","fail")]]
            summary=AnchoredOffsetbox(loc=loc,child=VPacker(children=rows,align="left",pad=0,sep=6),frameon=True,pad=.5,borderpad=.6)
            summary.patch.set(facecolor="white",edgecolor="none",alpha=.9);summary.set_zorder(60);summary.set_gid("qc-summary");axis.add_artist(summary)
            notable=[check for check in validation.get("checks",[]) if check.get("status") in {"WARN","FAIL"}]
            if notable:
                items=[TextArea(text["evidence"],textprops={"fontsize":16,"fontweight":"bold"})]
                for check in notable[:6]:
                    raw=f"{check['status']}: {check.get('check','')} — {check.get('details','')}"
                    shortened=raw[:110]+("…" if len(raw)>110 else "")
                    wrapped=textwrap.fill(shortened,width=42)
                    items.append(TextArea(wrapped,textprops={"fontsize":12,"color":style["colors"]["fail" if check["status"]=="FAIL" else "warn"]}))
                evidence=AnchoredOffsetbox(loc="lower right" if loc=="upper left" else "upper left",child=VPacker(children=items,align="left",pad=0,sep=7),frameon=True,pad=.5,borderpad=.6)
                evidence.patch.set(facecolor="white",edgecolor="none",alpha=.93);evidence.set_zorder(60);evidence.set_gid("qc-evidence");axis.add_artist(evidence)
            layers=[{"id":"topology-context","source":"build_result.artifacts.reaches_vector and subbasins_clipped","display_transform":"existing topology context, real basin and pale-gray subbasin boundaries","palette":"subbasins-water"},
                    {"id":"qc-summary","source":"validation_result.checks","display_transform":f"existing check counts PASS={counts.get('PASS',0)}, WARN={counts.get('WARN',0)}, FAIL={counts.get('FAIL',0)}; inset in map","palette":"qc-status"}]
            if notable:layers.append({"id":"qc-evidence","source":"validation_result.checks","display_transform":"first six existing WARN/FAIL only; complete details retained upstream","palette":"qc-status"})
            return save_figure(figure,output_dir,"hydrobase-qc-dashboard",language,text["qc_title"],source_results,layers,meta["crs"],extent,warnings,status=="success")
        return save_figure(figure,output_dir,"hydrobase-topology",language,text["topology_title"],source_results,
            [{"id":"subbasins","source":"build_result.artifacts.subbasins_clipped","display_transform":"stable IDs; display clipped to real basin; pale-gray boundaries from existing raster IDs","palette":"subbasins"},
             {"id":"reaches","source":"build_result.artifacts.reaches_vector","display_transform":"existing Strahler order, directions and outlet flags; selected labels","palette":"water"},
             {"id":"basin-boundary","source":"build_result.inputs.basin_boundary","display_transform":"real boundary; projected WGS84 graticules; inward geographic ticks; scale; black-white north arrow","palette":"black"},
             {"id":"subbasin-count","source":"build_result.artifacts.subbasins_table","display_transform":f"unique existing sub_id count={count}; inset label","palette":"black"}],meta["crs"],extent,warnings,status=="success")
