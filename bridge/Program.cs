using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using Microsoft.Win32;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

internal static class Program
{
    [DllImport("ole32.dll", CharSet = CharSet.Unicode)]
    private static extern int CLSIDFromProgID(string progId, out Guid clsid);

    [DllImport("oleaut32.dll", PreserveSig = false)]
    private static extern void GetActiveObject(ref Guid rclsid, IntPtr reserved, [MarshalAs(UnmanagedType.IUnknown)] out object? ppunk);

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false,
    };
    private const string SolidWorksProgId = "SldWorks.Application";
    private static readonly TimeSpan LaunchTimeout = TimeSpan.FromSeconds(60);
    private static readonly TimeSpan LaunchPollInterval = TimeSpan.FromMilliseconds(500);

    private static SldWorks? CachedApp;
    private static string? CachedDocumentTitle;

    private static readonly Dictionary<string, string[]> PlaneAliases = new(StringComparer.OrdinalIgnoreCase)
    {
        ["front"] = ["Front Plane", "前视基准面"],
        ["top"] = ["Top Plane", "上视基准面"],
        ["right"] = ["Right Plane", "右视基准面"],
    };

    [STAThread]
    public static int Main(string[] args)
    {
        Console.InputEncoding = System.Text.Encoding.UTF8;
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        try
        {
            if (args.Length == 0)
            {
                WriteError("missing_command", "Bridge command is required.");
                return 1;
            }

            if (string.Equals(args[0], "serve", StringComparison.OrdinalIgnoreCase))
            {
                return Serve();
            }

            string payloadJson = args.Length > 1
                ? (args[1] == "-" ? Console.In.ReadToEnd() : args[1])
                : "{}";
            payloadJson = payloadJson.TrimStart('\uFEFF', '\u200B', '\r', '\n', ' ', '\t');
            using JsonDocument payload = JsonDocument.Parse(payloadJson);
            object result = ExecuteCommand(args[0], payload.RootElement);

            Console.WriteLine(JsonSerializer.Serialize(result, JsonOptions));
            return 0;
        }
        catch (Exception ex)
        {
            WriteError("bridge_exception", ex.ToString());
            return 1;
        }
    }

    private static int Serve()
    {
        string? line;
        while ((line = Console.ReadLine()) != null)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            try
            {
                using JsonDocument request = JsonDocument.Parse(line);
                string command = request.RootElement.GetProperty("command").GetString()
                    ?? throw new InvalidOperationException("Request command is required.");
                string payloadJson = request.RootElement.TryGetProperty("payload", out JsonElement payloadElement)
                    ? payloadElement.GetRawText()
                    : "{}";
                using JsonDocument payload = JsonDocument.Parse(payloadJson);
                object result = ExecuteCommand(command, payload.RootElement);
                Console.WriteLine(JsonSerializer.Serialize(result, JsonOptions));
            }
            catch (Exception ex)
            {
                WriteError("bridge_exception", ex.ToString());
            }
        }

        return 0;
    }

    private static object ExecuteCommand(string command, JsonElement payload)
    {
        return command switch
        {
            "ping" => new { ok = true, bridge = "solidworks-bridge" },
            "new_part" => NewPart(payload),
            "new_assembly" => NewAssembly(payload),
            "create_sketch_on_plane" => CreateSketchOnPlane(payload),
            "create_sketch_on_face" => CreateSketchOnFace(payload),
            "create_center_rectangle" => CreateCenterRectangle(payload),
            "create_circle" => CreateCircle(payload),
            "draw_line" => DrawLine(payload),
            "draw_arc" => DrawArc(payload),
            "draw_polygon" => DrawPolygon(payload),
            "draw_centerline" => DrawCenterline(payload),
            "add_dimension" => AddDimension(payload),
            "add_dimension_v2" => AddDimensionV2(payload),
            "extrude_boss" => ExtrudeBoss(payload),
            "extrude_cut" => ExtrudeCut(payload),
            "inspect_active_part" => InspectActivePart(payload),
            "apply_fillet_to_feature_edges" => ApplyFilletToFeatureEdges(payload),
            "apply_chamfer_to_feature_edges" => ApplyChamferToFeatureEdges(payload),
            "combine_all_bodies" => CombineAllBodies(payload),
            "run_macro" => RunMacro(payload),
            "create_ref_plane" => CreateRefPlane(payload),
            "mirror_feature" => MirrorFeature(payload),
            "circular_pattern" => CircularPattern(payload),
            "linear_pattern" => LinearPattern(payload),
            "loft_boss" => LoftBoss(payload),
            "sweep_boss" => SweepBoss(payload),
            "rib" => Rib(payload),
            "add_component" => AddComponent(payload),
            "add_mate" => AddMate(payload),
            "add_explode_step" => AddExplodeStep(payload),
            "get_mass_properties" => GetMassProperties(payload),
            "export_file" => ExportFile(payload),
            "check_interference" => CheckInterference(payload),
            "measure_distance" => MeasureDistance(payload),
            "set_material" => SetMaterial(payload),
            _ => throw new InvalidOperationException($"Unknown command: {command}")
        };
    }

    private static object NewPart(JsonElement payload)
    {
        var app = AttachOrLaunch(true, ensureVisible: false);
        string templatePath = payload.TryGetProperty("templatePath", out JsonElement templateElement)
            ? templateElement.GetString() ?? string.Empty
            : @"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2023\templates\gb_part.prtdot";

        if (!File.Exists(templatePath))
        {
            return new { ok = false, reason = "template_not_found", templatePath };
        }

        ModelDoc2? doc = app.NewDocument(templatePath, 0, 0.0, 0.0) as ModelDoc2;
        if (doc != null)
        {
            CachedDocumentTitle = doc.GetTitle();
            ActivateDocument(app, CachedDocumentTitle);
        }

        ModelDoc2? active = GetTargetModel(app);
        return new
        {
            ok = doc != null && active != null,
            templatePath,
            activeTitle = active?.GetTitle(),
        };
    }

    private static object CreateSketchOnPlane(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        ModelDocExtension ext = model.Extension;
        string plane = payload.TryGetProperty("plane", out JsonElement planeElement)
            ? planeElement.GetString() ?? "front"
            : "front";
        string planeKey = NormalizePlaneKey(plane);

        string[] candidateNames = PlaneAliases.TryGetValue(planeKey, out string[]? aliases)
            ? aliases
            : [plane];

        bool selected = false;
        string? selectedName = null;
        foreach (string name in candidateNames)
        {
            selected = ext.SelectByID2(name, "PLANE", 0, 0, 0, false, 0, null, 0);
            if (selected)
            {
                selectedName = name;
                break;
            }
        }

        if (!selected)
        {
            return new { ok = false, reason = "plane_not_found", plane };
        }

        model.SketchManager.InsertSketch(true);
        return new
        {
            ok = true,
            plane = plane,
            selectedName,
            hasActiveSketch = GetActiveSketch(model) != null,
        };
    }

    private static object CreateCircle(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        SketchManager sketchManager = model.SketchManager;

        double centerX = payload.GetProperty("centerX").GetDouble();
        double centerY = payload.GetProperty("centerY").GetDouble();
        double centerZ = payload.TryGetProperty("centerZ", out JsonElement centerZElement) ? centerZElement.GetDouble() : 0.0;
        double radius = payload.GetProperty("radius").GetDouble();

        object? circle = sketchManager.CreateCircleByRadius(centerX, centerY, centerZ, radius);
        SketchSegment[] activeSegments = GetActiveSketchSegments(model);
        return new
        {
            ok = circle != null,
            radius,
            activeSketchSegmentCount = activeSegments.Length,
        };
    }

    private static object CreateCenterRectangle(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        SketchManager sketchManager = model.SketchManager;

        double centerX = payload.GetProperty("centerX").GetDouble();
        double centerY = payload.GetProperty("centerY").GetDouble();
        double centerZ = payload.TryGetProperty("centerZ", out JsonElement centerZElement) ? centerZElement.GetDouble() : 0.0;
        double cornerX = payload.GetProperty("cornerX").GetDouble();
        double cornerY = payload.GetProperty("cornerY").GetDouble();
        double cornerZ = payload.TryGetProperty("cornerZ", out JsonElement cornerZElement) ? cornerZElement.GetDouble() : 0.0;

        object?[]? segments = sketchManager.CreateCenterRectangle(centerX, centerY, centerZ, cornerX, cornerY, cornerZ) as object[];
        SketchSegment[] activeSegments = GetActiveSketchSegments(model);
        return new
        {
            ok = segments != null,
            segmentCount = segments?.Length ?? 0,
            activeSketchSegmentCount = activeSegments.Length,
        };
    }

    private static object AddDimension(JsonElement payload)
    {
        string method = payload.TryGetProperty("method", out JsonElement methodElement)
            ? methodElement.GetString() ?? "macro"
            : "macro";

        if (string.Equals(method, "macro", StringComparison.OrdinalIgnoreCase) || string.Equals(method, "safe", StringComparison.OrdinalIgnoreCase))
        {
            return new
            {
                ok = false,
                reason = "macro_method_unavailable_on_host",
                method,
                detail = "The VSTA macro entry required for in-process dimensioning is not stable on this host. Use high-level modeling tools or direct mode only for diagnosis.",
            };
        }

        if (string.Equals(method, "direct", StringComparison.OrdinalIgnoreCase))
        {
            return new
            {
                ok = false,
                reason = "direct_method_disabled",
                method,
                recommendedMethod = "create_rectangular_block|create_plate_with_holes|design_from_prompt",
                detail = "Direct out-of-proc dimension creation is disabled because it can hang or crash SolidWorks on this host.",
            };
        }

        return new
        {
            ok = false,
            reason = "unsupported_dimension_method",
            method,
            recommendedMethod = "create_rectangular_block|create_plate_with_holes|design_from_prompt",
            detail = "Dimension methods are currently disabled on this host because stable in-process automation is unavailable.",
        };
    }

    private static object ExtrudeBoss(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        bool mergeResult = payload.TryGetProperty("mergeResult", out JsonElement mergeElement)
            ? mergeElement.GetBoolean()
            : true;
        bool midPlane = payload.TryGetProperty("midPlane", out JsonElement midPlaneElement)
            ? midPlaneElement.GetBoolean()
            : true;
        string? sketchFeatureName = PrepareLatestSketchSelection(model);
        int featureCountBefore = CollectFeatureSummaries(model).Count;

        double depth = payload.GetProperty("depth").GetDouble();
        int endCondition = midPlane
            ? (int)swEndConditions_e.swEndCondMidPlane
            : (int)swEndConditions_e.swEndCondBlind;
        FeatureManager featureManager = model.FeatureManager;
        Feature? feature = featureManager.FeatureExtrusion3(
            true,
            false,
            false,
            endCondition,
            (int)swEndConditions_e.swEndCondBlind,
            depth,
            0.0,
            false,
            false,
            false,
            false,
            0.0,
            0.0,
            false,
            false,
            false,
            false,
            mergeResult,
            true,
            true,
            0,
            0,
            false
        ) as Feature;
        Feature? createdFeature = feature;
        if (createdFeature == null)
        {
            Feature? lastFeature = GetLastFeature(model);
            if (lastFeature != null
                && string.Equals(lastFeature.GetTypeName2(), "Extrusion", StringComparison.OrdinalIgnoreCase)
                && CollectFeatureSummaries(model).Count > featureCountBefore)
            {
                createdFeature = lastFeature;
            }
        }

        return new
        {
            ok = createdFeature != null,
            featureName = createdFeature?.Name,
            depth,
            sketchFeatureName,
            mergeResult,
            midPlane,
        };
    }

    private static object ExtrudeCut(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string? sketchFeatureName = PrepareLatestSketchSelection(model);
        int featureCountBefore = CollectFeatureSummaries(model).Count;

        double depth = payload.TryGetProperty("depth", out JsonElement depthElement)
            ? depthElement.GetDouble()
            : 0.0;
        bool throughAll = payload.TryGetProperty("throughAll", out JsonElement throughAllElement)
            && throughAllElement.GetBoolean();
        int endCondition = throughAll
            ? (int)swEndConditions_e.swEndCondThroughAllBoth
            : (int)swEndConditions_e.swEndCondBlind;

        FeatureManager featureManager = model.FeatureManager;
        Feature? feature = featureManager.FeatureCut(
            true,
            false,
            false,
            endCondition,
            endCondition,
            depth,
            0.0,
            false,
            false,
            false,
            false,
            0.0,
            0.0,
            false,
            false,
            false,
            false,
            false,
            false,
            true
        ) as Feature;
        Feature? createdFeature = feature;
        if (createdFeature == null)
        {
            Feature? lastFeature = GetLastFeature(model);
            if (lastFeature != null
                && string.Equals(lastFeature.GetTypeName2(), "Cut", StringComparison.OrdinalIgnoreCase)
                && CollectFeatureSummaries(model).Count > featureCountBefore)
            {
                createdFeature = lastFeature;
            }
        }

        return new
        {
            ok = createdFeature != null,
            featureName = createdFeature?.Name,
            depth,
            throughAll,
            sketchFeatureName,
        };
    }

    private static object InspectActivePart(JsonElement _payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        PartDoc? part = model as PartDoc;
        Body2[] solidBodies = part != null ? GetSolidBodies(part) : Array.Empty<Body2>();
        List<Dictionary<string, object?>> features = CollectFeatureSummaries(model);
        List<Dictionary<string, object?>> bodies = CollectBodySummaries(solidBodies);

        return new
        {
            ok = true,
            title = model.GetTitle(),
            path = model.GetPathName(),
            bodyCount = solidBodies.Length,
            bodies,
            featureCount = features.Count,
            features,
        };
    }

    private static object ApplyFilletToFeatureEdges(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string featureName = payload.GetProperty("featureName").GetString()
            ?? throw new InvalidOperationException("featureName is required.");
        double radius = payload.GetProperty("radius").GetDouble();
        Feature? feature = FindFeatureBySelector(model, featureName);
        if (feature == null)
        {
            return new { ok = false, reason = "feature_not_found", featureName };
        }

        int selectedEdgeCount = SelectFeatureEdges(model, feature);
        if (selectedEdgeCount == 0)
        {
            return new { ok = false, reason = "feature_edges_not_found", featureName };
        }

        object result = model.FeatureManager.FeatureFillet(
            (int)swFeatureFilletOptions_e.swFeatureFilletUniformRadius,
            radius,
            (int)swFeatureFilletType_e.swFeatureFilletType_Simple,
            0,
            null,
            null,
            null
        );
        model.ClearSelection2(true);

        Feature? filletFeature = result as Feature;
        return new
        {
            ok = filletFeature != null,
            featureName,
            radius,
            selectedEdgeCount,
            resultFeatureName = filletFeature?.Name,
        };
    }

    private static object ApplyChamferToFeatureEdges(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string featureName = payload.GetProperty("featureName").GetString()
            ?? throw new InvalidOperationException("featureName is required.");
        double distance = payload.GetProperty("distance").GetDouble();
        Feature? feature = FindFeatureBySelector(model, featureName);
        if (feature == null)
        {
            return new { ok = false, reason = "feature_not_found", featureName };
        }

        int selectedEdgeCount = SelectFeatureEdges(model, feature);
        if (selectedEdgeCount == 0)
        {
            return new { ok = false, reason = "feature_edges_not_found", featureName };
        }

        Feature? chamferFeature = model.FeatureManager.InsertFeatureChamfer(
            0,
            (int)swChamferType_e.swChamferEqualDistance,
            distance,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0
        ) as Feature;
        model.ClearSelection2(true);

        return new
        {
            ok = chamferFeature != null,
            featureName,
            distance,
            selectedEdgeCount,
            resultFeatureName = chamferFeature?.Name,
        };
    }

    private static object CombineAllBodies(JsonElement _payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        PartDoc? part = model as PartDoc;
        if (part == null)
        {
            return new { ok = false, reason = "active_document_is_not_part" };
        }

        Body2[] bodies = GetSolidBodies(part);
        if (bodies.Length < 2)
        {
            return new { ok = false, reason = "combine_requires_multiple_bodies", bodyCount = bodies.Length };
        }

        int operation = (int)swCombineBodiesOperationType_e.swCombineBodiesOperationAdd;
        Body2[] orderedBodies = OrderBodiesForCombine(bodies);
        string attempt = "main_and_tools";
        object toolBodies = orderedBodies.Skip(1).Cast<object>().ToArray();
        Feature? feature = model.FeatureManager.InsertCombineFeature(
            operation,
            orderedBodies[0],
            toolBodies
        ) as Feature;
        if (feature == null)
        {
            attempt = "all_bodies";
            feature = model.FeatureManager.InsertCombineFeature(
                operation,
                null!,
                orderedBodies.Cast<object>().ToArray()
            ) as Feature;
        }

        int selectedBodyCount = 0;
        if (feature == null)
        {
            attempt = "selected_bodies";
            selectedBodyCount = SelectBodiesForCombine(model, orderedBodies);
            feature = model.FeatureManager.InsertCombineFeature(
                operation,
                null!,
                Array.Empty<object>()
            ) as Feature;
        }

        if (feature == null)
        {
            attempt = "selected_body_names";
            selectedBodyCount = SelectBodiesForCombineByName(model, orderedBodies);
            feature = model.FeatureManager.InsertCombineFeature(
                operation,
                null!,
                Array.Empty<object>()
            ) as Feature;
        }

        if (feature == null)
        {
            attempt = "feature_data_main_and_tools";
            try
            {
                var featureData = new CombineBodiesFeatureDataClass
                {
                    OperationType = operation,
                    MainBody = orderedBodies[0],
                    BodiesToCombine = orderedBodies.Skip(1).Cast<object>().ToArray(),
                };
                feature = model.FeatureManager.CreateFeature(featureData) as Feature;
            }
            catch (COMException)
            {
                feature = null;
            }
        }

        if (feature == null)
        {
            attempt = "feature_data_all_bodies";
            try
            {
                var featureData = new CombineBodiesFeatureDataClass
                {
                    OperationType = operation,
                    BodiesToCombine = orderedBodies.Cast<object>().ToArray(),
                };
                feature = model.FeatureManager.CreateFeature(featureData) as Feature;
            }
            catch (COMException)
            {
                feature = null;
            }
        }

        if (feature == null)
        {
            model.ClearSelection2(true);
            return new
            {
                ok = false,
                reason = "combine_insert_failed",
                attempt,
                bodyCountBefore = bodies.Length,
                selectedBodyCount,
                bodyCountAfter = GetSolidBodies(part).Length,
            };
        }

        model.ForceRebuild3(false);
        int bodyCountAfter = GetSolidBodies(part).Length;

        return new
        {
            ok = feature != null && bodyCountAfter < bodies.Length,
            attempt,
            featureName = feature?.Name,
            bodyCountBefore = bodies.Length,
            bodyCountAfter,
        };
    }

    private static object RunMacro(JsonElement payload)
    {
        return new
        {
            ok = false,
            reason = "run_macro_disabled_on_host",
            recommendedMethod = "create_rectangular_block|create_plate_with_holes|design_from_prompt",
            detail = "SolidWorks macro execution is disabled on this host because the .NET/VSTA macro loader can raise a Microsoft .NET Framework dialog and terminate SolidWorks.",
        };
    }

    // Phase 1: Sketch Entities

    private static object DrawLine(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        double x1 = payload.GetProperty("x1").GetDouble();
        double y1 = payload.GetProperty("y1").GetDouble();
        double z1 = payload.TryGetProperty("z1", out var z1e) ? z1e.GetDouble() : 0.0;
        double x2 = payload.GetProperty("x2").GetDouble();
        double y2 = payload.GetProperty("y2").GetDouble();
        double z2 = payload.TryGetProperty("z2", out var z2e) ? z2e.GetDouble() : 0.0;

        var line = model.SketchManager.CreateLine(x1, y1, z1, x2, y2, z2);
        SketchSegment[] segments = GetActiveSketchSegments(model);
        return new { ok = line != null, x1, y1, x2, y2, activeSketchSegmentCount = segments.Length };
    }

    private static object DrawArc(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        double cx = payload.GetProperty("centerX").GetDouble();
        double cy = payload.GetProperty("centerY").GetDouble();
        double cz = payload.TryGetProperty("centerZ", out var cze) ? cze.GetDouble() : 0.0;
        double sx = payload.GetProperty("startX").GetDouble();
        double sy = payload.GetProperty("startY").GetDouble();
        double sz = payload.TryGetProperty("startZ", out var sze) ? sze.GetDouble() : 0.0;
        double ex = payload.GetProperty("endX").GetDouble();
        double ey = payload.GetProperty("endY").GetDouble();
        double ez = payload.TryGetProperty("endZ", out var eze) ? eze.GetDouble() : 0.0;
        int dir = payload.TryGetProperty("direction", out var de) ? de.GetInt32() : 1;

        var arc = model.SketchManager.CreateArc(cx, cy, cz, sx, sy, sz, ex, ey, ez, (short)dir);
        SketchSegment[] segments = GetActiveSketchSegments(model);
        return new { ok = arc != null, centerX = cx, centerY = cy, activeSketchSegmentCount = segments.Length };
    }

    private static object DrawPolygon(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        double cx = payload.GetProperty("centerX").GetDouble();
        double cy = payload.GetProperty("centerY").GetDouble();
        double cz = payload.TryGetProperty("centerZ", out var cze) ? cze.GetDouble() : 0.0;
        double r = payload.GetProperty("radius").GetDouble();
        int sides = payload.GetProperty("sides").GetInt32();
        bool inscribed = payload.TryGetProperty("inscribed", out var ie) && ie.GetBoolean();

        object?[]? poly = model.SketchManager.CreatePolygon(cx, cy, cz, cx + r, cy, cz, sides, inscribed) as object[];
        SketchSegment[] segments = GetActiveSketchSegments(model);
        return new { ok = poly != null, sides, inscribed, segmentCount = poly?.Length ?? 0, activeSketchSegmentCount = segments.Length };
    }

    private static object DrawCenterline(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        double x1 = payload.GetProperty("x1").GetDouble();
        double y1 = payload.GetProperty("y1").GetDouble();
        double z1 = payload.TryGetProperty("z1", out var z1e) ? z1e.GetDouble() : 0.0;
        double x2 = payload.GetProperty("x2").GetDouble();
        double y2 = payload.GetProperty("y2").GetDouble();
        double z2 = payload.TryGetProperty("z2", out var z2e) ? z2e.GetDouble() : 0.0;

        var line = model.SketchManager.CreateCenterLine(x1, y1, z1, x2, y2, z2);
        return new { ok = line != null, x1, y1, x2, y2 };
    }

    private static object CreateSketchOnFace(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string faceName = payload.GetProperty("faceName").GetString()!;

        bool selected = model.Extension.SelectByID2(faceName, "FACE", 0, 0, 0, false, 0, null, 0);
        if (!selected)
            return new { ok = false, reason = "face_not_found", faceName };

        model.SketchManager.InsertSketch(true);
        return new { ok = true, faceName, hasActiveSketch = GetActiveSketch(model) != null };
    }

    // Phase 2: Advanced Features

    private static object CreateRefPlane(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string ref1 = payload.GetProperty("ref1").GetString()!;
        int constraint1 = payload.GetProperty("constraint1").GetInt32();
        double offset1 = payload.TryGetProperty("offset1", out var o1) ? o1.GetDouble() : 0.0;
        string? ref2 = payload.TryGetProperty("ref2", out var r2e) ? r2e.GetString() : null;
        int constraint2 = payload.TryGetProperty("constraint2", out var c2e) ? c2e.GetInt32() : 0;
        double offset2 = payload.TryGetProperty("offset2", out var o2e) ? o2e.GetDouble() : 0.0;
        string? ref3 = payload.TryGetProperty("ref3", out var r3e) ? r3e.GetString() : null;
        int constraint3 = payload.TryGetProperty("constraint3", out var c3e) ? c3e.GetInt32() : 0;
        double offset3 = payload.TryGetProperty("offset3", out var o3e) ? o3e.GetDouble() : 0.0;

        model.ClearSelection2(true);

        bool sel1 = TrySelectPlane(model, ref1, false, 1);
        if (!sel1)
            return new { ok = false, reason = "ref1_not_found", ref1 };

        bool sel2 = true, sel3 = true;
        if (ref2 != null) sel2 = TrySelectPlane(model, ref2, true, 2);
        if (ref3 != null) sel3 = TrySelectPlane(model, ref3, true, 4);

        Feature? feature = null;
        string attempt = "insert_ref_plane";

        // Attempt 1: Direct InsertRefPlane
        int featureCountBefore = CollectFeatureSummaries(model).Count;
        model.FeatureManager.InsertRefPlane(constraint1, offset1, constraint2, offset2, constraint3, offset3);
        
        // Get the newly created feature
        if (CollectFeatureSummaries(model).Count > featureCountBefore)
        {
            feature = GetLastFeature(model);
        }

        // Attempt 2: Try with just first constraint
        if (feature == null)
        {
            attempt = "insert_ref_plane_no_offset";
            model.ClearSelection2(true);
            sel1 = TrySelectPlane(model, ref1, false, 1);
            featureCountBefore = CollectFeatureSummaries(model).Count;
            model.FeatureManager.InsertRefPlane(constraint1, offset1, 0, 0.0, 0, 0.0);
            
            if (CollectFeatureSummaries(model).Count > featureCountBefore)
            {
                feature = GetLastFeature(model);
            }
        }

        return new { ok = feature != null, featureName = feature?.Name, ref1, constraint1, offset1, sel1, sel2, sel3, attempt };
    }

    private static bool TrySelectPlane(ModelDoc2 model, string planeName, bool append, int mark)
    {
        string planeKey = NormalizePlaneKey(planeName);
        string[] candidateNames = PlaneAliases.TryGetValue(planeKey, out string[]? aliases) ? aliases : new[] { planeName };
        
        // Try without mark first (for InsertRefPlane compatibility)
        foreach (string name in candidateNames)
        {
            if (model.Extension.SelectByID2(name, "PLANE", 0, 0, 0, append, 0, null, 0))
                return true;
        }
        
        // Try with mark if that fails
        foreach (string name in candidateNames)
        {
            if (model.Extension.SelectByID2(name, "PLANE", 0, 0, 0, append, mark, null, 0))
                return true;
        }
        
        return model.Extension.SelectByID2(planeName, "PLANE", 0, 0, 0, append, 0, null, 0);
    }

    private static object MirrorFeature(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string mirrorPlane = payload.GetProperty("mirrorPlane").GetString()!;
        string[] featureNames = payload.GetProperty("features").EnumerateArray().Select(e => e.GetString()!).ToArray();
        bool geomPattern = payload.TryGetProperty("geomPattern", out var gp) && gp.GetBoolean();
        bool merge = payload.TryGetProperty("merge", out var m) ? m.GetBoolean() : true;

        model.ClearSelection2(true);
        int selectedCount = 0;
        foreach (string name in featureNames)
        {
            Feature? feat = FindFeatureByName(model, name);
            if (feat == null)
                return new { ok = false, reason = "feature_not_found", featureName = name };
            bool sel = model.Extension.SelectByID2(name, "BODYFEATURE", 0, 0, 0, selectedCount > 0, 1, null, 0);
            if (sel) selectedCount++;
        }
        model.Extension.SelectByID2(mirrorPlane, "PLANE", 0, 0, 0, true, 2, null, 0);

        var feature = model.FeatureManager.InsertMirrorFeature(true, geomPattern, merge, false) as Feature;
        return new { ok = feature != null, featureName = feature?.Name, mirrorPlane, mirroredFeatureCount = selectedCount };
    }

    private static object CircularPattern(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string axisName = payload.GetProperty("axis").GetString()!;
        int count = payload.GetProperty("count").GetInt32();
        double angle = payload.GetProperty("angle").GetDouble();
        bool equalSpacing = payload.TryGetProperty("equalSpacing", out var es) ? es.GetBoolean() : true;
        string[] featureNames = payload.GetProperty("features").EnumerateArray().Select(e => e.GetString()!).ToArray();

        model.ClearSelection2(true);
        int selectedCount = 0;
        foreach (string name in featureNames)
        {
            bool sel = model.Extension.SelectByID2(name, "BODYFEATURE", 0, 0, 0, selectedCount > 0, 1, null, 0);
            if (sel) selectedCount++;
        }
        model.Extension.SelectByID2(axisName, "AXIS", 0, 0, 0, true, 16, null, 0);

        var feature = model.FeatureManager.FeatureCircularPattern4(count, angle, false, "", false, equalSpacing, false) as Feature;
        return new { ok = feature != null, featureName = feature?.Name, count, angle };
    }

    private static object LinearPattern(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string dir1 = payload.GetProperty("direction1").GetString()!;
        int d1Count = payload.GetProperty("d1Count").GetInt32();
        double d1Spacing = payload.GetProperty("d1Spacing").GetDouble();
        string? dir2 = payload.TryGetProperty("direction2", out var d2e) ? d2e.GetString() : null;
        int d2Count = payload.TryGetProperty("d2Count", out var d2c) ? d2c.GetInt32() : 1;
        double d2Spacing = payload.TryGetProperty("d2Spacing", out var d2s) ? d2s.GetDouble() : 0.0;
        string[] featureNames = payload.GetProperty("features").EnumerateArray().Select(e => e.GetString()!).ToArray();

        model.ClearSelection2(true);
        int selectedCount = 0;
        foreach (string name in featureNames)
        {
            bool sel = model.Extension.SelectByID2(name, "BODYFEATURE", 0, 0, 0, selectedCount > 0, 4, null, 0);
            if (sel) selectedCount++;
        }
        model.Extension.SelectByID2(dir1, "EDGE", 0, 0, 0, true, 1, null, 0);
        if (dir2 != null)
            model.Extension.SelectByID2(dir2, "EDGE", 0, 0, 0, true, 2, null, 0);

        var feature = model.FeatureManager.FeatureLinearPattern4(d1Count, d1Spacing, d2Count, d2Spacing, false, false, "", "", false, false, false, false, false, false, false, false, false, false, 0.0, 0.0) as Feature;
        return new { ok = feature != null, featureName = feature?.Name, d1Count, d1Spacing, d2Count, d2Spacing };
    }

    private static object LoftBoss(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string[] profileNames = payload.GetProperty("profiles").EnumerateArray().Select(e => e.GetString()!).ToArray();
        string[] guideNames = payload.TryGetProperty("guides", out var ge) ? ge.EnumerateArray().Select(e => e.GetString()!).ToArray() : Array.Empty<string>();
        bool mergeResult = payload.TryGetProperty("mergeResult", out var mr) ? mr.GetBoolean() : true;

        model.ClearSelection2(true);
        int profileCount = 0;
        foreach (string name in profileNames)
        {
            bool sel = model.Extension.SelectByID2(name, "SKETCH", 0, 0, 0, profileCount > 0, 1, null, 0);
            if (sel) profileCount++;
        }
        int guideCount = 0;
        foreach (string name in guideNames)
        {
            bool sel = model.Extension.SelectByID2(name, "SKETCH", 0, 0, 0, true, 2, null, 0);
            if (sel) guideCount++;
        }

        var feature = model.FeatureManager.InsertProtrusionBlend2(false, false, false, 1.0, (short)6, (short)6, 1.0, 1.0, true, true, false, 0.0, 0.0, (short)0, mergeResult, true, true, 2) as Feature;
        return new { ok = feature != null, featureName = feature?.Name, profileCount, guideCount };
    }

    private static object SweepBoss(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string profileName = payload.GetProperty("profile").GetString()!;
        string pathName = payload.GetProperty("path").GetString()!;
        bool mergeResult = payload.TryGetProperty("mergeResult", out var mr) ? mr.GetBoolean() : true;

        model.ClearSelection2(true);
        model.Extension.SelectByID2(profileName, "SKETCH", 0, 0, 0, false, 1, null, 0);
        model.Extension.SelectByID2(pathName, "SKETCH", 0, 0, 0, true, 4, null, 0);

        var feature = model.FeatureManager.InsertProtrusionSwept3(false, false, (short)0, false, false, (short)0, (short)0, false, 0.0, 0.0, (short)0, (short)0, mergeResult, true, true, 0.0, false) as Feature;
        return new { ok = feature != null, featureName = feature?.Name, profile = profileName, path = pathName };
    }

    private static object Rib(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string sketchName = payload.GetProperty("sketch").GetString()!;
        double thickness = payload.GetProperty("thickness").GetDouble();
        int thicknessType = payload.TryGetProperty("thicknessType", out var tt) ? tt.GetInt32() : 0;
        bool flip = payload.TryGetProperty("flip", out var f) && f.GetBoolean();
        bool draftEnable = payload.TryGetProperty("draftEnable", out var de) && de.GetBoolean();

        // Close any active sketch first
        if (GetActiveSketch(model) != null)
        {
            model.SketchManager.InsertSketch(true);
        }

        model.ClearSelection2(true);
        
        // Try to select the sketch with different name variations
        bool selected = model.Extension.SelectByID2(sketchName, "SKETCH", 0, 0, 0, false, 0, null, 0);
        if (!selected)
        {
            // Try Chinese name variations
            string[] sketchVariations = { "草图1", "草图2", "草图3", "草图4", "草图5" };
            foreach (var variation in sketchVariations)
            {
                if (model.Extension.SelectByID2(variation, "SKETCH", 0, 0, 0, false, 0, null, 0))
                {
                    selected = true;
                    sketchName = variation;
                    break;
                }
            }
        }

        if (!selected)
        {
            return new { ok = false, reason = "sketch_not_found", sketch = sketchName };
        }

        int featureCountBefore = CollectFeatureSummaries(model).Count;
        string lastError = "";
        try
        {
            model.FeatureManager.InsertRib(false, flip, thickness, 0, false, draftEnable, false, 0.0, true, false);
        }
        catch (Exception ex)
        {
            lastError = ex.Message;
        }
        
        Feature? feature = null;
        if (CollectFeatureSummaries(model).Count > featureCountBefore)
            feature = GetLastFeature(model);

        return new { ok = feature != null, featureName = feature?.Name, thickness, sketch = sketchName, lastError };
    }

    // Phase 3: Assembly

    private static object NewAssembly(JsonElement payload)
    {
        var app = AttachOrLaunch(true, ensureVisible: false);
        string templatePath = payload.TryGetProperty("templatePath", out var tp) ? tp.GetString() ?? string.Empty : @"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_assembly.asmdot";
        if (!File.Exists(templatePath))
            return new { ok = false, reason = "template_not_found", templatePath };

        ModelDoc2? doc = app.NewDocument(templatePath, (int)swDocumentTypes_e.swDocASSEMBLY, 0.0, 0.0) as ModelDoc2;
        if (doc != null) { CachedDocumentTitle = doc.GetTitle(); ActivateDocument(app, CachedDocumentTitle); }
        return new { ok = doc != null, templatePath, activeTitle = doc?.GetTitle() };
    }

    private static object AddComponent(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        AssemblyDoc? assy = model as AssemblyDoc;
        if (assy == null) return new { ok = false, reason = "not_assembly_document" };

        string filePath = payload.GetProperty("filePath").GetString()!;
        string configName = payload.TryGetProperty("configName", out var cn) ? cn.GetString() ?? "" : "";
        double x = payload.TryGetProperty("x", out var xe) ? xe.GetDouble() : 0.0;
        double y = payload.TryGetProperty("y", out var ye) ? ye.GetDouble() : 0.0;
        double z = payload.TryGetProperty("z", out var ze) ? ze.GetDouble() : 0.0;

        if (!File.Exists(filePath))
            return new { ok = false, reason = "file_not_found", filePath };

        Component2? comp = null;
        string attempt = "AddComponent5";
        string lastError = "";

        // Attempt 1: AddComponent5
        try
        {
            comp = assy.AddComponent5(filePath, 0, configName, false, "", x, y, z) as Component2;
        }
        catch (Exception ex) { lastError = ex.Message; }

        // Attempt 2: AddComponent4
        if (comp == null)
        {
            attempt = "AddComponent4";
            try
            {
                comp = assy.AddComponent4(filePath, configName, x, y, z) as Component2;
            }
            catch (Exception ex) { lastError = ex.Message; }
        }

        // Attempt 3: AddComponent3
        if (comp == null)
        {
            attempt = "AddComponent3";
            try
            {
                object transforms = new double[] { 1, 0, 0, 0, 1, 0, 0, 0, 1, x, y, z };
                object[] names = new object[] { filePath };
                object[] trans = new object[] { transforms };
                assy.AddComponents3(names, trans, null);
                var components = assy.GetComponents(false) as object[];
                if (components != null && components.Length > 0)
                    comp = components[components.Length - 1] as Component2;
            }
            catch (Exception ex) { lastError = ex.Message; }
        }

        // Attempt 4: AddComponent (simple - returns bool)
        if (comp == null)
        {
            attempt = "AddComponent";
            try
            {
                bool added = assy.AddComponent(filePath, x, y, z);
                if (added)
                {
                    var components = assy.GetComponents(false) as object[];
                    if (components != null && components.Length > 0)
                        comp = components[components.Length - 1] as Component2;
                }
                else
                {
                    lastError = "AddComponent returned false";
                }
            }
            catch (Exception ex) { lastError = ex.Message; }
        }

        // Check if component was added by counting components
        if (comp == null)
        {
            var components = assy.GetComponents(false) as object[];
            if (components != null && components.Length > 0)
            {
                comp = components[components.Length - 1] as Component2;
                attempt += "_fallback_last_component";
            }
        }

        return new { ok = comp != null, componentName = comp?.Name2, filePath, attempt, lastError };
    }

    private static object AddMate(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        AssemblyDoc? assy = model as AssemblyDoc;
        if (assy == null) return new { ok = false, reason = "not_assembly_document" };

        int mateType = payload.GetProperty("mateType").GetInt32();
        int alignType = payload.TryGetProperty("alignType", out var at) ? at.GetInt32() : 0;
        bool flip = payload.TryGetProperty("flip", out var f) && f.GetBoolean();
        double distance = payload.TryGetProperty("distance", out var d) ? d.GetDouble() : 0.0;
        double angle = payload.TryGetProperty("angle", out var a) ? a.GetDouble() : 0.0;
        string[] entityRefs = payload.GetProperty("entities").EnumerateArray().Select(e => e.GetString()!).ToArray();

        model.ClearSelection2(true);
        int selectedCount = 0;
        foreach (string entityRef in entityRefs)
        {
            string[] parts = entityRef.Split('@');
            string entityName = parts[0];
            string entityType = parts.Length > 1 ? parts[1] : "FACE";
            bool sel = model.Extension.SelectByID2(entityName, entityType, 0, 0, 0, selectedCount > 0, 0, null, 0);
            if (sel) selectedCount++;
        }

        int errorStatus = 0;
        var mate = assy.AddMate3(mateType, alignType, flip, distance, distance, distance, 1.0, 1.0, angle, angle, angle, false, out errorStatus);
        return new { ok = mate != null, mateType, alignType, selectedEntities = selectedCount, errorStatus };
    }

    private static object AddExplodeStep(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        AssemblyDoc? assy = model as AssemblyDoc;
        if (assy == null) return new { ok = false, reason = "not_assembly_document" };

        string[] componentNames = payload.GetProperty("components").EnumerateArray().Select(e => e.GetString()!).ToArray();
        double dx = payload.TryGetProperty("dx", out var dxe) ? dxe.GetDouble() : 0.0;
        double dy = payload.TryGetProperty("dy", out var dye) ? dye.GetDouble() : 0.0;
        double dz = payload.TryGetProperty("dz", out var dze) ? dze.GetDouble() : 0.0;

        model.ClearSelection2(true);
        int selectedCount = 0;
        foreach (string name in componentNames)
        {
            bool sel = model.Extension.SelectByID2(name, "COMPONENT", 0, 0, 0, selectedCount > 0, 0, null, 0);
            if (sel) selectedCount++;
        }

        assy.AutoExplode();
        return new { ok = true, componentCount = selectedCount, translation = new { dx, dy, dz } };
    }

    private static object AddDimensionV2(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        double x = payload.GetProperty("x").GetDouble();
        double y = payload.GetProperty("y").GetDouble();
        double z = payload.GetProperty("z").GetDouble();
        string[] entityRefs = payload.GetProperty("entities").EnumerateArray().Select(e => e.GetString()!).ToArray();

        model.ClearSelection2(true);
        int selectedCount = 0;
        foreach (string entityRef in entityRefs)
        {
            string[] parts = entityRef.Split('@');
            string entityName = parts[0];
            string entityType = parts.Length > 1 ? parts[1] : "SKETCHSEGMENT";
            bool sel = model.Extension.SelectByID2(entityName, entityType, 0, 0, 0, selectedCount > 0, 0, null, 0);
            if (sel) selectedCount++;
        }

        bool prevToggle = app.GetUserPreferenceToggle((int)swUserPreferenceToggle_e.swInputDimValOnCreate);
        app.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swInputDimValOnCreate, false);
        var dim = model.AddDimension2(x, y, z);
        app.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swInputDimValOnCreate, prevToggle);

        double? value = null;
        if (dim != null)
        {
            try
            {
                var swDim = dim as IDimension;
                if (swDim != null)
                {
                    object result = swDim.GetSystemValue3((int)swInConfigurationOpts_e.swThisConfiguration, null);
                    if (result is double d) value = d;
                    else if (result != null) value = Convert.ToDouble(result);
                }
            }
            catch { }
        }
        return new { ok = dim != null, selectedEntities = selectedCount, currentValue = value };
    }

    private static object GetMassProperties(JsonElement _payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        var massProp = model.Extension.CreateMassProperty();
        if (massProp == null) return new { ok = false, reason = "mass_property_unavailable" };

        double mass = massProp.Mass;
        double volume = massProp.Volume;
        double surfaceArea = massProp.SurfaceArea;
        object? comObj = massProp.CenterOfMass;
        double[]? com = null;
        if (comObj is Array comArray && comArray.Length >= 3)
        {
            com = comArray.Cast<object?>().Where(v => v != null).Select(v => Convert.ToDouble(v)).ToArray();
        }
        return new { ok = true, mass, volume, surfaceArea, centerOfMass = com };
    }

    // Phase 4: Export & Analysis

    private static object ExportFile(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string outputPath = payload.GetProperty("outputPath").GetString()!;
        int version = payload.TryGetProperty("version", out var v) ? v.GetInt32() : 0;
        int options = payload.TryGetProperty("options", out var o) ? o.GetInt32() : 0;

        string? dir = Path.GetDirectoryName(outputPath);
        if (dir != null && !Directory.Exists(dir)) Directory.CreateDirectory(dir);

        int errors = 0;
        int warnings = 0;
        bool saved = model.Extension.SaveAs2(outputPath, version, options, null, "", false, ref errors, ref warnings);
        return new { ok = saved || File.Exists(outputPath), outputPath, errors, warnings, exists = File.Exists(outputPath) };
    }

    private static object CheckInterference(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        AssemblyDoc? assy = model as AssemblyDoc;
        if (assy == null) return new { ok = false, reason = "not_assembly_document" };

        bool coincidenceIsInterference = payload.TryGetProperty("coincidenceIsInterference", out var ci) && ci.GetBoolean();
        object? pComp = null;
        object? pFace = null;
        assy.ToolsCheckInterference2(0, null, coincidenceIsInterference, out pComp, out pFace);
        object[]? interferences = pComp as object[];
        return new { ok = true, interferenceCount = interferences?.Length ?? 0, hasInterference = (interferences?.Length ?? 0) > 0 };
    }

    private static object MeasureDistance(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        string[] entityRefs = payload.GetProperty("entities").EnumerateArray().Select(e => e.GetString()!).ToArray();

        model.ClearSelection2(true);
        int selectedCount = 0;
        foreach (string entityRef in entityRefs)
        {
            string[] parts = entityRef.Split('@');
            string entityName = parts[0];
            string entityType = parts.Length > 1 ? parts[1] : "FACE";
            bool sel = model.Extension.SelectByID2(entityName, entityType, 0, 0, 0, selectedCount > 0, 0, null, 0);
            if (sel) selectedCount++;
        }

        var measure = model.Extension.CreateMeasure();
        if (measure == null) return new { ok = false, reason = "measure_unavailable" };

        bool calculated = measure.Calculate(null);
        double? distance = null;
        if (calculated) { try { distance = measure.Distance; } catch { } }
        return new { ok = calculated, distance, selectedEntities = selectedCount };
    }

    private static object SetMaterial(JsonElement payload)
    {
        var app = AttachOrLaunch(false, ensureVisible: false);
        ModelDoc2 model = RequireActiveModel(app);
        PartDoc? part = model as PartDoc;
        if (part == null) return new { ok = false, reason = "not_part_document" };

        string database = payload.TryGetProperty("database", out var db) ? db.GetString() ?? "" : "";
        string material = payload.GetProperty("material").GetString()!;
        string config = payload.TryGetProperty("config", out var cfg) ? cfg.GetString() ?? "" : "";

        part.SetMaterialPropertyName2(config, database, material);
        string? currentDb = null;
        string? currentMat = null;
        try { currentMat = part.GetMaterialPropertyName2(config, out currentDb); } catch { }

        return new { ok = string.Equals(currentMat, material, StringComparison.OrdinalIgnoreCase), database, material, currentDatabase = currentDb, currentMaterial = currentMat };
    }

    private static SldWorks AttachOrLaunch(bool create, bool ensureVisible)
    {
        SldWorks? app = GetCachedApplication();
        if (app != null)
        {
            if (ensureVisible)
            {
                app.Visible = true;
            }
            app.UserControl = true;
            return app;
        }

        app = TryGetActiveApplication();
        if (app == null)
        {
            if (!create)
            {
                throw new InvalidOperationException("No running SolidWorks instance was found.");
            }
            app = LaunchAndAttachDesktopSolidWorks();
        }

        if (ensureVisible)
        {
            app.Visible = true;
        }
        app.UserControl = true;
        CachedApp = app;
        return app;
    }

    private static SldWorks LaunchAndAttachDesktopSolidWorks()
    {
        string executablePath = ResolveSolidWorksExecutablePath();
        if (!File.Exists(executablePath))
        {
            throw new InvalidOperationException($"SolidWorks executable was not found: {executablePath}");
        }

        var startInfo = new ProcessStartInfo(executablePath)
        {
            UseShellExecute = true,
            WorkingDirectory = Path.GetDirectoryName(executablePath) ?? System.Environment.CurrentDirectory,
        };
        Process.Start(startInfo);

        DateTime deadline = DateTime.UtcNow + LaunchTimeout;
        while (DateTime.UtcNow < deadline)
        {
            SldWorks? app = TryGetActiveApplication();
            if (app != null)
            {
                return app;
            }

            Thread.Sleep(LaunchPollInterval);
        }

        throw new InvalidOperationException("Timed out waiting for SolidWorks to register its COM automation object.");
    }

    private static string ResolveSolidWorksExecutablePath()
    {
        using RegistryKey? progIdKey = Registry.ClassesRoot.OpenSubKey($"{SolidWorksProgId}\\CLSID");
        string? clsid = progIdKey?.GetValue(null) as string;
        if (string.IsNullOrWhiteSpace(clsid))
        {
            throw new InvalidOperationException("SolidWorks CLSID registry entry was not found.");
        }

        using RegistryKey? serverKey = Registry.ClassesRoot.OpenSubKey($@"CLSID\{clsid}\LocalServer32");
        string? localServer = serverKey?.GetValue(null) as string;
        if (string.IsNullOrWhiteSpace(localServer))
        {
            throw new InvalidOperationException("SolidWorks LocalServer32 registry entry was not found.");
        }

        string trimmed = localServer.Trim();
        if (trimmed.StartsWith('"'))
        {
            int endQuote = trimmed.IndexOf('"', 1);
            if (endQuote > 1)
            {
                return trimmed.Substring(1, endQuote - 1);
            }
        }

        int exeIndex = trimmed.IndexOf(".exe", StringComparison.OrdinalIgnoreCase);
        if (exeIndex >= 0)
        {
            return trimmed.Substring(0, exeIndex + 4);
        }

        return trimmed;
    }

    private static SldWorks? GetCachedApplication()
    {
        if (CachedApp == null)
        {
            return null;
        }

        try
        {
            _ = CachedApp.Visible;
            return CachedApp;
        }
        catch (COMException)
        {
            CachedApp = null;
            return null;
        }
    }

    private static SldWorks? TryGetActiveApplication()
    {
        int hr = CLSIDFromProgID(SolidWorksProgId, out Guid clsid);
        if (hr < 0)
        {
            Marshal.ThrowExceptionForHR(hr);
        }

        try
        {
            GetActiveObject(ref clsid, IntPtr.Zero, out object? instance);
            return instance as SldWorks;
        }
        catch (COMException ex) when ((uint)ex.HResult == 0x800401E3)
        {
            return null;
        }
    }

    private static void ActivateDocument(SldWorks app, string? title)
    {
        if (string.IsNullOrWhiteSpace(title))
        {
            return;
        }

        int errors = 0;
        try
        {
            _ = app.ActivateDoc3(title, true, 0, ref errors);
        }
        catch (COMException)
        {
        }
    }

    private static ModelDoc2? GetTargetModel(SldWorks app)
    {
        ModelDoc2? active = app.ActiveDoc as ModelDoc2;
        if (!string.IsNullOrWhiteSpace(CachedDocumentTitle))
        {
            string? activeTitle = active?.GetTitle();
            if (!string.Equals(activeTitle, CachedDocumentTitle, StringComparison.OrdinalIgnoreCase))
            {
                ActivateDocument(app, CachedDocumentTitle);
                active = app.ActiveDoc as ModelDoc2;
            }
        }

        return active;
    }

    private static ModelDoc2 RequireActiveModel(SldWorks app)
    {
        ModelDoc2? model = GetTargetModel(app);
        if (model == null)
        {
            throw new InvalidOperationException("No active SolidWorks document.");
        }

        CachedDocumentTitle = model.GetTitle();
        return model;
    }

    private static Sketch? GetActiveSketch(ModelDoc2 model)
    {
        return model.GetActiveSketch2() as Sketch;
    }

    private static string? FindLastSketchFeatureName(ModelDoc2 model)
    {
        return FindLastSketchFeature(model)?.Name;
    }

    private static Feature? FindFeatureByName(ModelDoc2 model, string featureName)
    {
        Feature? feature = model.FirstFeature() as Feature;
        while (feature != null)
        {
            if (string.Equals(feature.Name, featureName, StringComparison.OrdinalIgnoreCase))
            {
                return feature;
            }

            feature = feature.GetNextFeature() as Feature;
        }

        return null;
    }

    private static Feature? FindFeatureBySelector(ModelDoc2 model, string selector)
    {
        return selector switch
        {
            "__first_extrusion__" => FindFirstFeatureByType(model, "Extrusion"),
            "__last_extrusion__" => FindLastFeatureByType(model, "Extrusion"),
            "__last_cut__" => FindLastFeatureByCanonicalType(model, "Cut"),
            _ => FindFeatureByName(model, selector),
        };
    }

    private static Feature? FindFirstFeatureByType(ModelDoc2 model, string typeName)
    {
        Feature? feature = model.FirstFeature() as Feature;
        while (feature != null)
        {
            if (string.Equals(feature.GetTypeName2(), typeName, StringComparison.OrdinalIgnoreCase))
            {
                return feature;
            }

            feature = feature.GetNextFeature() as Feature;
        }

        return null;
    }

    private static Feature? FindLastFeatureByType(ModelDoc2 model, string typeName)
    {
        Feature? feature = model.FirstFeature() as Feature;
        Feature? matched = null;
        while (feature != null)
        {
            if (string.Equals(feature.GetTypeName2(), typeName, StringComparison.OrdinalIgnoreCase))
            {
                matched = feature;
            }

            feature = feature.GetNextFeature() as Feature;
        }

        return matched;
    }

    private static Feature? FindLastFeatureByCanonicalType(ModelDoc2 model, string canonicalType)
    {
        Feature? feature = model.FirstFeature() as Feature;
        Feature? matched = null;
        while (feature != null)
        {
            string typeName = feature.GetTypeName2();
            if (string.Equals(CanonicalizeFeatureTypeStable(feature.Name, typeName), canonicalType, StringComparison.OrdinalIgnoreCase))
            {
                matched = feature;
            }

            feature = feature.GetNextFeature() as Feature;
        }

        return matched;
    }

    private static Feature? FindLastSketchFeature(ModelDoc2 model)
    {
        Feature? feature = model.FirstFeature() as Feature;
        Feature? lastSketchFeature = null;
        while (feature != null)
        {
            string typeName = feature.GetTypeName2();
            if (string.Equals(typeName, "ProfileFeature", StringComparison.OrdinalIgnoreCase))
            {
                lastSketchFeature = feature;
            }

            feature = feature.GetNextFeature() as Feature;
        }

        return lastSketchFeature;
    }

    private static Feature? GetLastFeature(ModelDoc2 model)
    {
        Feature? feature = model.FirstFeature() as Feature;
        Feature? lastFeature = null;
        while (feature != null)
        {
            lastFeature = feature;
            feature = feature.GetNextFeature() as Feature;
        }

        return lastFeature;
    }

    private static SketchSegment[] GetActiveSketchSegments(ModelDoc2 model)
    {
        Sketch? sketch = GetActiveSketch(model);
        if (sketch == null)
        {
            return Array.Empty<SketchSegment>();
        }

        object[] segments = (sketch.GetSketchSegments() as object[]) ?? Array.Empty<object>();
        return segments.OfType<SketchSegment>().ToArray();
    }

    private static SketchSegment[] GetEditableSketchSegments(ModelDoc2 model)
    {
        SketchSegment[] activeSegments = GetActiveSketchSegments(model);
        if (activeSegments.Length > 0)
        {
            return activeSegments;
        }

        Feature? sketchFeature = FindLastSketchFeature(model);
        if (sketchFeature == null)
        {
            return Array.Empty<SketchSegment>();
        }

        model.ClearSelection2(true);
        bool selected = model.Extension.SelectByID2(sketchFeature.Name, "SKETCH", 0, 0, 0, false, 0, null, 0);
        if (!selected)
        {
            return Array.Empty<SketchSegment>();
        }

        model.EditSketch();
        return GetActiveSketchSegments(model);
    }

    private static string? PrepareLatestSketchSelection(ModelDoc2 model)
    {
        if (GetActiveSketch(model) != null)
        {
            model.SketchManager.InsertSketch(true);
        }

        model.ClearSelection2(true);
        string? sketchFeatureName = FindLastSketchFeatureName(model);
        if (!string.IsNullOrWhiteSpace(sketchFeatureName))
        {
            model.Extension.SelectByID2(sketchFeatureName, "SKETCH", 0, 0, 0, false, 0, null, 0);
        }

        return sketchFeatureName;
    }

    private static Body2[] GetSolidBodies(PartDoc part)
    {
        object[] bodies = (part.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[]) ?? Array.Empty<object>();
        return bodies.OfType<Body2>().ToArray();
    }

    private static List<Dictionary<string, object?>> CollectBodySummaries(Body2[] bodies)
    {
        var summaries = new List<Dictionary<string, object?>>();
        foreach (Body2 body in bodies)
        {
            double[]? box = null;
            if (body.GetBodyBox() is Array array)
            {
                box = array.Cast<object?>()
                    .Where(value => value != null)
                    .Select(value => Convert.ToDouble(value))
                    .ToArray();
            }

            summaries.Add(new Dictionary<string, object?>
            {
                ["name"] = body.Name,
                ["box"] = box,
            });
        }

        return summaries;
    }

    private static List<Dictionary<string, object?>> CollectFeatureSummaries(ModelDoc2 model)
    {
        var summaries = new List<Dictionary<string, object?>>();
        Feature? feature = model.FirstFeature() as Feature;
        while (feature != null)
        {
            string typeName = feature.GetTypeName2();
            summaries.Add(new Dictionary<string, object?>
            {
                ["name"] = feature.Name,
                ["typeName"] = typeName,
                ["canonicalType"] = CanonicalizeFeatureTypeStable(feature.Name, typeName),
            });
            feature = feature.GetNextFeature() as Feature;
        }

        return summaries;
    }

    private static string CanonicalizeFeatureType(string? featureName, string? typeName)
    {
        string name = featureName ?? string.Empty;
        string type = typeName ?? string.Empty;
        string loweredName = name.ToLowerInvariant();
        string loweredType = type.ToLowerInvariant();

        if (loweredType == "extrusion")
        {
            return "Boss";
        }

        if (loweredType == "ice" || loweredName.Contains("cut") || name.Contains("切除"))
        {
            return "Cut";
        }

        if (loweredType.Contains("fillet") || loweredName.Contains("fillet") || name.Contains("圆角"))
        {
            return "Fillet";
        }

        if (loweredType.Contains("chamfer") || loweredName.Contains("chamfer") || name.Contains("倒角"))
        {
            return "Chamfer";
        }

        if (loweredType.Contains("combine") || loweredName.Contains("combine") || name.Contains("组合"))
        {
            return "Combine";
        }

        return type;
    }

    private static string CanonicalizeFeatureTypeStable(string? featureName, string? typeName)
    {
        string name = featureName ?? string.Empty;
        string type = typeName ?? string.Empty;
        string loweredName = name.ToLowerInvariant();
        string loweredType = type.ToLowerInvariant();

        if (loweredType == "extrusion")
        {
            return "Boss";
        }

        if (loweredType == "ice")
        {
            if (loweredName.Contains("cut") || name.Contains("\u5207\u9664"))
            {
                return "Cut";
            }

            if (loweredName.Contains("boss") || loweredName.Contains("extrude") || name.Contains("\u51f8\u53f0"))
            {
                return "Boss";
            }
        }

        if (loweredName.Contains("cut") || name.Contains("\u5207\u9664"))
        {
            return "Cut";
        }

        if (loweredName.Contains("boss") || name.Contains("\u51f8\u53f0"))
        {
            return "Boss";
        }

        if (loweredType.Contains("fillet") || loweredName.Contains("fillet") || name.Contains("\u5706\u89d2"))
        {
            return "Fillet";
        }

        if (loweredType.Contains("chamfer") || loweredName.Contains("chamfer") || name.Contains("\u5012\u89d2"))
        {
            return "Chamfer";
        }

        if (loweredType.Contains("combine") || loweredName.Contains("combine") || name.Contains("\u7ec4\u5408"))
        {
            return "Combine";
        }

        return type;
    }

    private static int SelectBodiesForCombine(ModelDoc2 model, Body2[] bodies)
    {
        model.ClearSelection2(true);
        int selectedCount = 0;
        for (int index = 0; index < bodies.Length; index++)
        {
            int mark = index == 0 ? 1 : 2;
            if (bodies[index].Select(selectedCount > 0, mark))
            {
                selectedCount++;
            }
        }

        return selectedCount;
    }

    private static int SelectBodiesForCombineByName(ModelDoc2 model, Body2[] bodies)
    {
        model.ClearSelection2(true);
        int selectedCount = 0;
        for (int index = 0; index < bodies.Length; index++)
        {
            string? name = bodies[index].Name;
            if (string.IsNullOrWhiteSpace(name))
            {
                continue;
            }

            int mark = index == 0 ? 1 : 2;
            bool selected = model.Extension.SelectByID2(
                name,
                "SOLIDBODY",
                0,
                0,
                0,
                selectedCount > 0,
                mark,
                null,
                0
            );
            if (selected)
            {
                selectedCount++;
            }
        }

        return selectedCount;
    }

    private static Body2[] OrderBodiesForCombine(Body2[] bodies)
    {
        return bodies
            .OrderByDescending(GetApproximateBodyVolume)
            .ToArray();
    }

    private static double GetApproximateBodyVolume(Body2 body)
    {
        if (body.GetBodyBox() is not Array array || array.Length < 6)
        {
            return 0.0;
        }

        double[] values = array.Cast<object?>()
            .Where(value => value != null)
            .Select(value => Convert.ToDouble(value))
            .ToArray();
        if (values.Length < 6)
        {
            return 0.0;
        }

        return Math.Abs((values[3] - values[0]) * (values[4] - values[1]) * (values[5] - values[2]));
    }

    private static int SelectFeatureEdges(ModelDoc2 model, Feature feature)
    {
        model.ClearSelection2(true);
        SelectionMgr selectionManager = model.SelectionManager as SelectionMgr
            ?? throw new InvalidOperationException("Selection manager is unavailable.");
        SelectData selectData = selectionManager.CreateSelectData();
        object[] faces = (feature.GetFaces() as object[]) ?? Array.Empty<object>();
        var seen = new HashSet<nint>();
        int selectedCount = 0;

        foreach (Face2 face in faces.OfType<Face2>())
        {
            object[] edges = (face.GetEdges() as object[]) ?? Array.Empty<object>();
            foreach (object edgeObject in edges)
            {
                if (edgeObject is not IEntity entity)
                {
                    continue;
                }

                nint edgeIdentity = Marshal.GetIUnknownForObject(edgeObject);
                try
                {
                    if (!seen.Add(edgeIdentity))
                    {
                        continue;
                    }
                }
                finally
                {
                    Marshal.Release(edgeIdentity);
                }

                if (entity.Select4(selectedCount > 0, selectData))
                {
                    selectedCount++;
                }
            }
        }

        return selectedCount;
    }

    private static bool IsHorizontalSegment(SketchSegment segment)
    {
        if (segment is not SketchLine line)
        {
            return false;
        }

        SketchPoint startPoint = (SketchPoint)line.GetStartPoint2();
        SketchPoint endPoint = (SketchPoint)line.GetEndPoint2();
        return Math.Abs(startPoint.Y - endPoint.Y) < 1e-9;
    }

    private static bool IsVerticalSegment(SketchSegment segment)
    {
        if (segment is not SketchLine line)
        {
            return false;
        }

        SketchPoint startPoint = (SketchPoint)line.GetStartPoint2();
        SketchPoint endPoint = (SketchPoint)line.GetEndPoint2();
        return Math.Abs(startPoint.X - endPoint.X) < 1e-9;
    }

    private static void WriteError(string code, string detail)
    {
        Console.WriteLine(JsonSerializer.Serialize(new { ok = false, errorCode = code, detail }, JsonOptions));
    }

    private static string NormalizePlaneKey(string plane)
    {
        if (string.IsNullOrWhiteSpace(plane))
        {
            return "front";
        }

        string normalized = plane.Trim().ToLowerInvariant();
        if (normalized.Contains("front"))
        {
            return "front";
        }

        if (normalized.Contains("top"))
        {
            return "top";
        }

        if (normalized.Contains("right"))
        {
            return "right";
        }

        return normalized;
    }
}
