"""Paper-style calibration figures and explicit comparison alignment."""
from __future__ import annotations
import json
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


def attach_comparisons(frame, manifest_path, data_shape):
    if not manifest_path:
        return []
    manifest_path=manifest_path.resolve()
    doc=json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if doc.get("schema_version") != "1.0" or not isinstance(doc.get("series"),list) or not doc["series"]:
        raise ValueError("comparison manifest requires schema_version=1.0 and nonempty series")
    keys=doc.get("join_on")
    if keys not in (["time"],["event_id","time"],["event_id","step"]):
        raise ValueError("join_on must explicitly identify time or event_id + time/step")
    if data_shape == "event_collection" and "event_id" not in keys:
        raise ValueError("event comparisons require event_id")
    if not set(keys).issubset(frame) or frame.duplicated(keys).any():
        raise ValueError("primary series has missing or duplicate alignment keys")
    paths=[manifest_path];labels=frame.attrs["simulation_labels"]
    for index,item in enumerate(doc["series"],1):
        label=item.get("label")
        if not isinstance(label,str) or not label.strip() or label in labels.values():
            raise ValueError("comparison labels must be nonempty and unique")
        if item.get("unit") != frame.attrs["unit"] or item.get("split") != "validation":
            raise ValueError("comparison unit/split must match primary validation series")
        raw=Path(item["path"]);path=raw.resolve() if raw.is_absolute() else (manifest_path.parent/raw).resolve()
        other=pd.read_csv(path);column=item.get("column","simulated")
        if not set(keys+[column]).issubset(other) or other.duplicated(keys).any():
            raise ValueError("comparison series has missing columns or duplicate keys")
        left=frame[keys].copy();right=other[keys+[column]].copy()
        for key in keys:
            if key=="time":
                for values in [left[key],right[key]]:
                    if values.isna().any():raise ValueError("missing time alignment keys")
                left_aware={pd.Timestamp(value).tzinfo is not None for value in left[key]}
                right_aware={pd.Timestamp(value).tzinfo is not None for value in right[key]}
                if len(left_aware)!=1 or left_aware != right_aware:
                    raise ValueError("comparison timestamp timezone awareness must match primary series")
                left[key]=pd.to_datetime(left[key],utc=True,errors="raise")
                right[key]=pd.to_datetime(right[key],utc=True,errors="raise")
            else:
                left[key]=left[key].astype(str);right[key]=right[key].astype(str)
        if left.duplicated(keys).any() or right.duplicated(keys).any():
            raise ValueError("duplicate normalized alignment keys")
        aligned=left.merge(right,on=keys,how="left",validate="one_to_one",indicator=True)
        if not aligned["_merge"].eq("both").all() or len(other)!=len(frame):
            raise ValueError("comparison coverage does not exactly match validation events/time")
        values=pd.to_numeric(aligned[column],errors="raise").to_numpy(float)
        if not np.isfinite(values).all():raise ValueError("comparison values must be finite")
        name=f"comparison_{index}";frame[name]=values;labels[name]=label;paths.append(path)
    return paths


def _helpers():
    # Import lazily to keep this supporting module usable by installed entrypoints.
    import render_model_calibration_atlas as main
    return main


def _axis(ax):
    ax.set_facecolor("white")
    for spine in ax.spines.values():spine.set_color("black");spine.set_linewidth(1.1)
    ax.tick_params(direction="in",labelsize=16)
    ax.grid(alpha=.25,color="#BBBBBB",linewidth=.5)
    ax.set_axisbelow(True)


def _flow_label(frame,language):
    unit=frame.attrs.get("unit", "unit unavailable")
    unit="m³/s" if unit in {"m3/s","m^3/s"} else unit
    return ("流量" if language=="zh" else "Discharge")+f" ({unit})"


def _nse(frame,column):
    scored=frame["scored"].to_numpy(bool)
    obs=frame["observed"].to_numpy(float)[scored];sim=frame[column].to_numpy(float)[scored]
    if len(obs)<2 or not np.isfinite(obs).all() or not np.isfinite(sim).all():
        return None,len(obs),"insufficient or nonfinite scored samples"
    denominator=float(np.sum((obs-obs.mean())**2))
    if denominator==0:return None,len(obs),"constant observations"
    return float(1-np.sum((sim-obs)**2)/denominator),len(obs),None


def render_overview(trace,best,cal,val,result,out,language,style,result_path,sources,warnings):
    m=_helpers();t=m.labels(language);c=style["colors"]
    fig,axes=plt.subplots(1,3,figsize=(12,8),dpi=200)
    axes[0].plot(trace["evaluation"],trace["best_so_far"],color=c["calibration"],lw=2)
    axes[0].set_xlabel(t["evaluation"]);axes[0].set_ylabel("最佳目标函数值" if language=="zh" else "Best objective value")
    names=list(best["parameters"]);axes[1].barh(names,[best["parameters"][n]["relative_position"] for n in names],color=c["validation"])
    axes[1].set_xlim(0,1);axes[1].set_xlabel(t["relative"]);axes[1].set_ylabel("参数" if language=="zh" else "Parameter")
    metrics=sorted(set(cal["metrics"])|set(val["metrics"]));x=np.arange(len(metrics));width=.38
    for shift,data,label,color in [(-width/2,cal,t["calibration"],c["calibration"]),(width/2,val,t["validation"],c["validation"])]:
        axes[2].bar(x+shift,[data["metrics"].get(k,np.nan) for k in metrics],width,label=label,color=color)
    axes[2].set_xticks(x,metrics,rotation=35,ha="right");axes[2].set_xlabel("指标" if language=="zh" else "Metric")
    axes[2].set_ylabel("指标值（各指标原单位）" if language=="zh" else "Value (native metric units)")
    axes[2].legend(frameon=False)
    for ax in axes:_axis(ax)
    fig.tight_layout(pad=1.4)
    return m.save_figure(fig,out/"calibration_overview",m.figure_meta("overview",language,t["overview"],result_path,sources,warnings,["upstream trace, parameter positions and metrics unchanged; no visible titles"]))


def process_figure(frame,stem,title,language,style,result_path,source,warnings):
    m=_helpers();t=m.labels(language);has_p="precipitation" in frame
    fig=plt.figure(figsize=(12,8),dpi=200)
    grid=fig.add_gridspec(2 if has_p else 1,1,height_ratios=[1,3] if has_p else [1],hspace=.08)
    ax=fig.add_subplot(grid[-1]);is_time="time" in frame
    if is_time:
        dates=pd.to_datetime(frame["time"],errors="raise")
        if dates.isna().any() or not dates.is_monotonic_increasing or dates.duplicated().any():
            raise ValueError("process timestamps must be valid, unique and ordered")
        x=dates.to_numpy();locator=mdates.AutoDateLocator(minticks=3,maxticks=6)
        ax.xaxis.set_major_locator(locator);ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        xlabel="时间" if language=="zh" else "Time"
    else:
        x=frame["step"].to_numpy() if "step" in frame else np.arange(len(frame))
        xlabel="时间步" if language=="zh" else "Time step"
    ax.plot(x,frame["observed"],color="black",lw=2,label="实测" if language=="zh" else "Observed",zorder=4)
    simulations=frame.attrs.get("simulation_labels",{"simulated":t["simulated"]});palette=style["simulation_palette"]
    if len(simulations)>len(palette):raise ValueError("at most eight simulations can use distinct palette colors")
    bindings=[]
    for index,(column,label) in enumerate(simulations.items()):
        nse,count,reason=_nse(frame,column)
        value="不可用" if language=="zh" else "unavailable"
        if nse is not None:value=f"{nse:.3f}"
        ax.plot(x,frame[column],lw=1.7,color=palette[index],label=f"{label} (NSE = {value})",zorder=3)
        bindings.append({"column":column,"label":label,"nse":nse,"scored_samples":count,"unavailable_reason":reason,"color":palette[index]})
    m.shade_unscored(ax,x,frame["scored"].to_numpy(),"#EEEEEE")
    ax.set_xlabel(xlabel);ax.set_ylabel(_flow_label(frame,language));_axis(ax)
    ax.legend(loc="best",frameon=True,facecolor="white",edgecolor="none",framealpha=.9)
    ax.margins(x=.01,y=.2)
    if has_p:
        rain=fig.add_subplot(grid[0],sharex=ax)
        width=.8 if not is_time or len(x)<2 else float(np.min(np.diff(mdates.date2num(x))))*.85
        rain.bar(x,frame["precipitation"],width=width,color="#9CCAE8",alpha=.65)
        rain.invert_yaxis();rain.set_ylabel("降水 (mm)" if language=="zh" else "Rainfall (mm)")
        rain.tick_params(labelbottom=False);_axis(rain)
    fig.subplots_adjust(left=.12,right=.98,bottom=.12,top=.98)
    sources=[source]+frame.attrs.get("comparison_sources",[])
    meta=m.figure_meta("validation-process",language,title,result_path,sources,warnings,["NSE = 1 - sum((sim-obs)^2)/sum((obs-mean(obs))^2), scored=true samples within this figure only; display diagnostic, upstream metrics unchanged","unscored samples shaded; explicit comparison keys; no visible titles"])
    meta["simulation_metrics"]=bindings;meta["unit"]=frame.attrs.get("unit");meta["scope"]={"split":"validation","event_ids":frame["event_id"].astype(str).unique().tolist() if "event_id" in frame else [],"scored_only":True}
    return m.save_figure(fig,stem,meta)


def render_scatter(frame,out,language,style,result_path,source,warnings):
    m=_helpers();t=m.labels(language);scored=frame[frame["scored"]];obs=scored["observed"].to_numpy(float)
    if not len(obs):raise ValueError("scatter requires scored validation samples")
    fig,ax=plt.subplots(figsize=(12,8),dpi=200);low=float(obs.min());high=float(obs.max())
    for index,(column,label) in enumerate(frame.attrs["simulation_labels"].items()):
        sim=scored[column].to_numpy(float);low=min(low,float(sim.min()));high=max(high,float(sim.max()))
        ax.scatter(obs,sim,s=22,alpha=.65,color=style["simulation_palette"][index],label=label)
    ax.plot([low,high],[low,high],"--",color="#555555",lw=1.5,label="1:1")
    flow=_flow_label(frame,language)
    ax.set_xlabel(("实测" if language=="zh" else "Observed")+" "+flow);ax.set_ylabel(t["simulated"]+" "+flow)
    ax.legend(frameon=False);_axis(ax);fig.tight_layout(pad=1.4)
    return m.save_figure(fig,out/"validation_scatter",m.figure_meta("validation-scatter",language,t["scatter"],result_path,[source]+frame.attrs.get("comparison_sources",[]),warnings,["validation scored=true rows only; multiple explicitly aligned simulations; 1:1 display reference; no visible titles"]))
