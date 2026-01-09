import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from math import isfinite
import time
from mathutils import Vector
import colorsys
from ctypes import c_void_p, c_float
from math import pi, sqrt, exp, hypot, sin, cos

def node_bounds(node, ui_scale):
    """
    计算节点边界框的 View2D 坐标
    """
    ax, ay = node.location.x, node.location.y
    p = node.parent
    while p:
        ax += p.location.x
        ay += p.location.y
        p = p.parent

    if node.hide:
        dpi_fac = bpy.context.preferences.system.dpi / 72
        magic_w_cloud_value = 9
        x_min = ax
        x_max = ax + node.width
        height = node.dimensions.y / dpi_fac
        y_min = ay - (height / 2 + magic_w_cloud_value)
        y_max = ay + (height / 2 - magic_w_cloud_value)
        x_min *= dpi_fac
        x_max *= dpi_fac
        y_min *= dpi_fac
        y_max *= dpi_fac
    else:
        x_min = ax * ui_scale
        x_max = x_min + node.dimensions.x
        y_max = ay * ui_scale
        y_min = y_max - node.dimensions.y

    return x_min, x_max, y_min, y_max


def get_rounded_rect_path(node, v2d, radius=4.0, resolution=12, thickness=0.0):
    ui_scale = bpy.context.preferences.system.ui_scale
    x_min, x_max, y_min, y_max = node_bounds(node, ui_scale)
    
    # 转换到 Region 像素坐标
    p_bl = v2d.view_to_region(x_min, y_min, clip=False)
    p_tr = v2d.view_to_region(x_max, y_max, clip=False)
    
    if not p_bl or not p_tr:
        return None
        
    rmin_x = min(p_bl[0], p_tr[0])
    rmax_x = max(p_bl[0], p_tr[0])
    rmin_y = min(p_bl[1], p_tr[1])
    rmax_y = max(p_bl[1], p_tr[1])
    
    # 向外扩张 (线宽的一半)
    offset = thickness * 0.5
    rmin_x -= offset
    rmax_x += offset
    rmin_y -= offset
    rmax_y += offset
    
    w = rmax_x - rmin_x
    h = rmax_y - rmin_y
    
    eff_radius = min(radius, w/2, h/2)
    
    path = []
    
    def add_arc(cx, cy, start_ang, end_ang):
        for i in range(resolution + 1):
            t = i / resolution
            ang = start_ang + (end_ang - start_ang) * t
            path.append((cx + eff_radius * cos(ang), cy + eff_radius * sin(ang)))

    path.append(((rmin_x + rmax_x)/2.0, rmin_y))
    add_arc(rmax_x - eff_radius, rmin_y + eff_radius, 1.5 * pi, 2.0 * pi)
    add_arc(rmax_x - eff_radius, rmax_y - eff_radius, 0.0, 0.5 * pi)
    add_arc(rmin_x + eff_radius, rmax_y - eff_radius, 0.5 * pi, 1.0 * pi)
    add_arc(rmin_x + eff_radius, rmin_y + eff_radius, 1.0 * pi, 1.5 * pi)
    path.append(((rmin_x + rmax_x)/2.0, rmin_y))
    
    return path

    
draw_handler = None
last_time = 0
_SHADER_CACHE = {}

# 用于存储固定的流状态
_locked_flow_data = {
    'links': set(),
    'nodes': set(),
    'is_locked': False
}

def get_shader(name):
    if name in _SHADER_CACHE:
        return _SHADER_CACHE[name]
    
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', 'ModelViewProjectionMatrix')
    
    # --- 公共 Vertex Source ---
    vert_src = '''
        void main() {
            gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);
            v_uv = uv;
        }
    '''

    if name == 'RAINBOW':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_rainbow_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('FLOAT', 'u_time')
        info.push_constant('FLOAT', 'u_alpha')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        
        info.fragment_source('''
            vec3 hsv2rgb(vec3 c) {
                vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
                vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
                return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
            }
            void main() {
                float v_progress = v_uv.x;
                float v_side = v_uv.y;
                float t = u_time * 0.5;
                float flow_phase = t * 3.0 - v_progress * 6.2831853; 
                float hue = fract(flow_phase / 6.2831853);
                
                float saturation = 0.9;
                float value = 0.95;
                float pulse_t = u_time * 1.0;
                float pulse_center = fract(pulse_t);
                float raw_dist = fract(pulse_center - v_progress);
                float pulse = 0.0;
                if (raw_dist > 0.5) {
                     float front_dist = 1.0 - raw_dist;
                     pulse = exp(-(front_dist * front_dist) / 0.0002);
                } else {
                     pulse = exp(-(raw_dist * raw_dist) / 0.08);
                }
                float boost = 1.0 + 0.6 * pulse;
                float sat_damp = 1.0 - 0.2 * pulse;
                
                vec3 rgb = hsv2rgb(vec3(hue, saturation * sat_damp, min(1.0, value * boost)));
                float dist = abs(v_side);
                float alpha_edge = 1.0 - smoothstep(0.85, 1.0, dist);
                fragColor = vec4(rgb, u_alpha * alpha_edge);
            }
        ''')

    # --- 动态颜色渐变 Shader (支持最多10个颜色) ---
    elif name == 'GRADIENT':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_gradient_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('FLOAT', 'u_time')
        info.push_constant('FLOAT', 'u_alpha')
        info.push_constant('INT', 'u_color_count')  # 实际使用的颜色数量
        
        # 接收最多10个颜色
        for i in range(10):
            info.push_constant('VEC4', f'color{i+1}')
        
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        
        info.fragment_source('''
            void main() {
                float v_progress = v_uv.x;
                float v_side = v_uv.y;
                float t = u_time * 0.5;
                
                // 构建颜色数组
                vec3 colors[10];
                colors[0] = color1.rgb;
                colors[1] = color2.rgb;
                colors[2] = color3.rgb;
                colors[3] = color4.rgb;
                colors[4] = color5.rgb;
                colors[5] = color6.rgb;
                colors[6] = color7.rgb;
                colors[7] = color8.rgb;
                colors[8] = color9.rgb;
                colors[9] = color10.rgb;
                
                // 计算流动相位
                float flow_speed = 0.5;
                float phase = (t * flow_speed) - v_progress;
                phase = fract(phase);
                
                // 动态颜色混合
                float n = float(u_color_count);
                float pos = phase * n;
                int index = int(floor(pos));
                float f = fract(pos);
                
                // 确保索引在有效范围内
                index = min(index, u_color_count - 1);
                int next_index = (index + 1) % u_color_count;
                
                vec3 final_base_rgb = mix(colors[index], colors[next_index], f);
                
                // 脉冲效果
                float pulse_t = u_time * 1.0;
                float pulse_center = fract(pulse_t);
                float raw_dist = fract(pulse_center - v_progress);
                float pulse = 0.0;
                if (raw_dist > 0.5) {
                     float front_dist = 1.0 - raw_dist;
                     pulse = exp(-(front_dist * front_dist) / 0.0002);
                } else {
                     pulse = exp(-(raw_dist * raw_dist) / 0.08);
                }
                float boost = 1.0 + 0.3 * pulse;
                
                vec3 final_rgb = min(vec3(1.0), final_base_rgb * boost);
                
                float dist = abs(v_side);
                float alpha_edge = 1.0 - smoothstep(0.85, 1.0, dist);
                fragColor = vec4(final_rgb, u_alpha * alpha_edge);
            }
        ''')

    elif name == 'SMOOTH_COLOR':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_smooth_color_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('VEC4', 'color')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        info.fragment_source('''
            void main() {
                float dist = abs(v_uv.y);
                float alpha_edge = 1.0 - smoothstep(0.85, 1.0, dist);
                fragColor = vec4(color.rgb, color.a * alpha_edge);
            }
        ''')
    
    elif name == 'SDF_CIRCLE':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_sdf_circle_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('VEC4', 'color')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        info.fragment_source('''
            void main() {
                float dist = length(v_uv);
                float delta = 1.5 * fwidth(dist);
                float alpha = 1.0 - smoothstep(1.0 - delta, 1.0, dist);
                fragColor = vec4(color.rgb, color.a * alpha);
            }
        ''')

    shader = gpu.shader.create_from_info(info)
    _SHADER_CACHE[name] = shader
    return shader


def _view2d_zoom_factor(v2d):
    try:
        x0, y0 = v2d.region_to_view(0.0, 0.0)
        x1, y1 = v2d.region_to_view(1.0, 0.0)
        dx = abs(x1 - x0)
        if dx <= 1e-8:
            return 1.0
        return 1.0 / dx
    except Exception:
        return 1.0

def get_curving_factor():
    try:
        curving = bpy.context.preferences.themes[0].node_editor.noodle_curving
        return curving / 10.0
    except Exception:
        return 0.0

def _rainbow_rgba(step_t, time_sec):
    t = time_sec * 0.5
    pulse_t = time_sec * 1.0
    pulse_center = pulse_t % 1.0
    flow_phase = t * 3.0 - step_t * 4.0
    hue = (flow_phase / (2 * pi)) % 1.0
    saturation = 0.65
    value = 0.95
    raw_dist = (pulse_center - step_t) % 1.0
    if raw_dist > 0.5:
        front_dist = 1.0 - raw_dist
        pulse = exp(-(front_dist * front_dist) / (2.0 * 0.01 * 0.01))
    else:
        pulse = exp(-(raw_dist * raw_dist) / (2.0 * 0.2 * 0.2))
    boost = 1.0 + 3.0 * pulse
    sat_damp = 1.0 - 0.95 * pulse
    r, g, b = colorsys.hsv_to_rgb(hue, saturation * sat_damp, min(1.0, value * boost))
    return (r, g, b, 1.0)

def get_native_link_points(link, v2d, curv):
    fs, ts = link.from_socket, link.to_socket
    try:
        if not (fs.enabled and ts.enabled):
            return None
        from_node = link.from_node
        to_node = link.to_node
        from_idx = _get_socket_index_cached({}, from_node, fs, True) or 0
        to_idx = _get_socket_index_cached({}, to_node, ts, False) or 0
        x1, y1 = get_socket_loc(from_node, True, from_idx)
        x2, y2 = get_socket_loc(to_node, False, to_idx)
    except Exception:
        return None

    y_off = 0
    y1 += y_off
    y2 += y_off

    if curv <= 0.001:
        p0 = (x1, y1)
        p3 = (x2, y2)
        p1 = (x1 + (x2 - x1) * (1.0 / 3.0), y1 + (y2 - y1) * (1.0 / 3.0))
        p2 = (x1 + (x2 - x1) * (2.0 / 3.0), y1 + (y2 - y1) * (2.0 / 3.0))
        v2r = v2d.view_to_region
        seg = 24
        pts = []
        for i in range(seg + 1):
            t = i / seg
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            pts.append(v2r(x, y, clip=False))
        return pts

    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    if dx != 0:
        slope = dy / dx
    else:
        slope = float('inf')

    curving_factor = curv * 10
    clamp_factor = min(1.0, slope * (4.5 - 0.25 * curving_factor))
    handle_offset = curving_factor * 0.1 * dx * clamp_factor

    p0 = (x1, y1)
    p3 = (x2, y2)
    p1 = (x1 + handle_offset, y1)
    p2 = (x2 - handle_offset, y2)
    
    v2r = v2d.view_to_region
    seg = 24
    pts = []
    for i in range(seg + 1):
        t = i / seg
        inv_t = 1 - t
        inv_t2 = inv_t * inv_t
        inv_t3 = inv_t2 * inv_t
        t2 = t * t
        t3 = t2 * t
        x = (inv_t3 * p0[0] + 3 * inv_t2 * t * p1[0] + 3 * inv_t * t2 * p2[0] + t3 * p3[0])
        y = (inv_t3 * p0[1] + 3 * inv_t2 * t * p1[1] + 3 * inv_t * t2 * p2[1] + t3 * p3[1])
        pts.append(v2r(x, y, clip=False))
    return pts

def dpi_fac():
    prefs = bpy.context.preferences.system
    return prefs.dpi / 72

def abs_node_location(node):
    abs_location = node.location
    if node.parent is None:
        return abs_location
    return abs_location + abs_node_location(node.parent)

def get_socket_loc(node, is_output, index):
    try:
        sockets = node.outputs if is_output else node.inputs
        if index < len(sockets):
            socket = sockets[index]
            offset = 520 
            if bpy.app.version >= (5, 1, 0): 
                offset = 456
            vec = Vector((c_float * 2).from_address(c_void_p.from_address(socket.as_pointer() + offset).value + 24))
            return vec.x, vec.y
    except Exception:
        pass

    if node.type == 'REROUTE':
        nlocx, nlocy = abs_node_location(node)
        fac = dpi_fac()
        return (nlocx + 1) * fac, (nlocy + 1) * fac

    fac = dpi_fac()
    nlocx, nlocy = abs_node_location(node)
    base_x = (nlocx + 1) * fac
    base_y = (nlocy + 1) * fac

    if is_output:
        x = base_x + node.dimensions.x
    else:
        x = base_x

    header_height = 32.0 * fac
    socket_height = 21.0 * fac
    y = base_y - header_height

    sockets = node.outputs if is_output else node.inputs
    enabled_sockets = [s for s in sockets if s.enabled]
    try:
        real_idx = enabled_sockets.index(sockets[index])
    except (ValueError, IndexError):
        real_idx = 0

    y = y - (real_idx * socket_height) - (socket_height * 0.5)
    if is_output:
        y += 0.5 * fac
    else:
        y -= 0.5 * fac
    return x, y

def _get_socket_index_cached(cache, node, socket, is_output):
    key = (node.as_pointer(), bool(is_output))
    socket_map = cache.get(key)
    if socket_map is None:
        sockets = node.outputs if is_output else node.inputs
        socket_map = {s.as_pointer(): i for i, s in enumerate(sockets)}
        cache[key] = socket_map
    return socket_map.get(socket.as_pointer())

def _get_line_strip_geometry(vertices, width):
    if len(vertices) < 2:
        return [], []
    pos_data = []
    uv_data = []
    half_w = width * 0.5
    count = len(vertices)
    verts = [Vector(v) for v in vertices]
    
    distances = [0.0]
    total_length = 0.0
    for i in range(count - 1):
        dist = (verts[i+1] - verts[i]).length
        total_length += dist
        distances.append(total_length)
    
    for i in range(count):
        curr_p = verts[i]
        if i == 0:
            tangent = (verts[1] - curr_p).normalized()
        elif i == count - 1:
            tangent = (curr_p - verts[i-1]).normalized()
        else:
            t1 = (curr_p - verts[i-1]).normalized()
            t2 = (verts[i+1] - curr_p).normalized()
            tangent = (t1 + t2).normalized()
        
        normal = Vector((-tangent.y, tangent.x))
        p0 = curr_p + normal * half_w
        p1 = curr_p - normal * half_w
        
        pos_data.append((p0.x, p0.y))
        pos_data.append((p1.x, p1.y))
        
        if total_length > 0:
            u = distances[i] / total_length
        else:
            u = 0.0
        uv_data.append((u, 1.0))
        uv_data.append((u, -1.0))
    return pos_data, uv_data

def draw_batch_lines(all_lines_data, shader_name, width, colors=None, time_sec=0.0):
    if not all_lines_data:
        return

    shader = get_shader(shader_name)
    if not shader:
        return

    all_pos = []
    all_uv = []
    
    for vertices in all_lines_data:
        if not vertices or len(vertices) < 2:
            continue
        pos, uv = _get_line_strip_geometry(vertices, width)
        if not pos:
            continue
        if all_pos:
            all_pos.append(all_pos[-1])
            all_uv.append(all_uv[-1])
            all_pos.append(pos[0])
            all_uv.append(uv[0])
        all_pos.extend(pos)
        all_uv.extend(uv)
        
    if not all_pos:
        return

    shader.bind()
    if shader_name == 'RAINBOW':
        shader.uniform_float("u_time", time_sec % 1000.0)
        shader.uniform_float("u_alpha", 1.0)
    elif shader_name == 'GRADIENT':
        shader.uniform_float("u_time", time_sec % 1000.0)
        shader.uniform_float("u_alpha", 1.0)
        
        # 获取颜色数量和颜色列表
        color_count = len(colors) if colors else 0
        if color_count < 2:
            # 如果颜色不足，使用默认值
            color_count = 5
            colors = [(0.0, 0.5, 1.0, 1.0), (0.0, 1.0, 0.8, 1.0), (1.0, 1.0, 0.0, 1.0),
                      (1.0, 0.5, 0.0, 1.0), (1.0, 0.0, 0.5, 1.0)]
        
        # 设置颜色数量
        shader.uniform_int("u_color_count", color_count)
        
        # 传入最多10个颜色（不足的用最后一个颜色填充）
        color_list = list(colors[:color_count])
        while len(color_list) < 10:
            color_list.append(color_list[-1] if color_list else (1.0, 1.0, 1.0, 1.0))
        
        for i in range(10):
            shader.uniform_float(f"color{i+1}", color_list[i])
    elif shader_name == 'SMOOTH_COLOR':
        if colors and len(colors) >= 1:
            shader.uniform_float("color", colors[0])
    
    batch = batch_for_shader(shader, 'TRI_STRIP', {"pos": all_pos, "uv": all_uv})
    batch.draw(shader)

def draw_batch_circles(batch_circles, radius, color):
    if not batch_circles or radius <= 0:
        return
    shader = get_shader('SDF_CIRCLE')
    if not shader:
        return
    adjusted_radius = radius * 1.0
    size = adjusted_radius + 2.0
    uv_scale = size / adjusted_radius if adjusted_radius > 0 else 1.0
    all_pos = []
    all_uv = []
    o0 = (-size, -size)
    o1 = ( size, -size)
    o2 = (-size,  size)
    o3 = ( size,  size)
    u0 = (-uv_scale, -uv_scale)
    u1 = ( uv_scale, -uv_scale)
    u2 = (-uv_scale,  uv_scale)
    u3 = ( uv_scale,  uv_scale)
    
    for (cx, cy) in batch_circles:
        p0 = (cx + o0[0], cy + o0[1])
        p1 = (cx + o1[0], cy + o1[1])
        p2 = (cx + o2[0], cy + o2[1])
        p3 = (cx + o3[0], cy + o3[1])
        all_pos.extend([p0, p2, p1])
        all_uv.extend([u0, u2, u1])
        all_pos.extend([p1, p2, p3])
        all_uv.extend([u1, u2, u3])
        
    batch = batch_for_shader(shader, 'TRIS', {"pos": all_pos, "uv": all_uv})
    shader.bind()
    shader.uniform_float("color", color)
    gpu.state.blend_set('ALPHA')
    batch.draw(shader)
    gpu.state.blend_set('NONE')

def get_panel_settings():
    try:
        scene = bpy.context.scene
        if hasattr(scene, 'colorful_connections_settings'):
            settings = scene.colorful_connections_settings
            # 辅助函数：转为RGBA
            def to_rgba(c):
                if isinstance(c, (list, tuple)):
                    if len(c) >= 3:
                        return (float(c[0]), float(c[1]), float(c[2]), 1.0)
                # 如果是 PropertyGroup 的属性
                if hasattr(c, 'color'):
                    col = c.color
                    return (float(col[0]), float(col[1]), float(col[2]), 1.0)
                return (1.0, 1.0, 1.0, 1.0)
            
            # 从新的颜色集合中读取
            gradient_colors = []
            color_count = getattr(settings, 'gradient_color_count', 5)
            colors = getattr(settings, 'gradient_colors', None)
            
            if colors and len(colors) > 0:
                # 只读取实际使用的颜色数量
                for i in range(min(color_count, len(colors))):
                    gradient_colors.append(to_rgba(colors[i]))
            
            # 如果颜色不足，使用默认值
            if len(gradient_colors) < 2:
                gradient_colors = [
                    (0.0, 0.5, 1.0, 1.0),
                    (0.0, 1.0, 0.8, 1.0),
                    (1.0, 1.0, 0.0, 1.0),
                    (1.0, 0.5, 0.0, 1.0),
                    (1.0, 0.0, 0.5, 1.0)
                ]
            
            return {
                'animation_speed': settings.animation_speed,
                'line_thickness': settings.line_thickness,
                'node_border_thickness': settings.node_border_thickness,
                'enable_colorful_connections': settings.enable_colorful_connections,
                'connection_color_type': settings.connection_color_type,
                'trace_mode': getattr(settings, 'trace_mode', 'ALL_SELECTED'),
                'flow_direction': getattr(settings, 'flow_direction', 'DOWNSTREAM'),
                'lock_flow': getattr(settings, 'lock_flow', False),
                'gradient_colors': gradient_colors
            }
        else:
            # 默认值
            return {
                'animation_speed': 1.0,
                'line_thickness': 2.0,
                'node_border_thickness': 3.0,
                'enable_colorful_connections': True,
                'connection_color_type': 'RAINBOW',
                'trace_mode': 'ALL_SELECTED',
                'flow_direction': 'DOWNSTREAM',
                'lock_flow': False,
                'gradient_colors': [
                    (0.0, 0.5, 1.0, 1.0),
                    (0.0, 1.0, 0.8, 1.0),
                    (1.0, 1.0, 0.0, 1.0),
                    (1.0, 0.5, 0.0, 1.0),
                    (1.0, 0.0, 0.5, 1.0)
                ]
            }
    except Exception as e:
        # 调试用
        # print(f"Error in get_panel_settings: {e}")
        return {
            'animation_speed': 1.0,
            'line_thickness': 2.0,
            'node_border_thickness': 3.0,
            'enable_colorful_connections': True,
            'connection_color_type': 'RAINBOW',
            'trace_mode': 'ALL_SELECTED',
            'flow_direction': 'DOWNSTREAM',
            'lock_flow': False,
            'gradient_colors': [
                (0.0, 0.5, 1.0, 1.0),
                (0.0, 1.0, 0.8, 1.0),
                (1.0, 1.0, 0.0, 1.0),
                (1.0, 0.5, 0.0, 1.0),
                (1.0, 0.0, 0.5, 1.0)
            ]
        }

def extend_links_through_reroutes(links_to_draw, start_node, direction='both', visited_nodes=None):
    if visited_nodes is None:
        visited_nodes = set()
    if start_node in visited_nodes or start_node.type != 'REROUTE':
        return
    visited_nodes.add(start_node)
    
    if direction in ['forward', 'both']:
        for output in start_node.outputs:
            if output.enabled:
                for link in output.links:
                    links_to_draw.add(link)
                    next_node = link.to_node
                    if next_node and next_node.type == 'REROUTE':
                        extend_links_through_reroutes(links_to_draw, next_node, 'forward', visited_nodes)
    
    if direction in ['backward', 'both']:
        for input_socket in start_node.inputs:
            if input_socket.enabled:
                for link in input_socket.links:
                    links_to_draw.add(link)
                    prev_node = link.from_node
                    if prev_node and prev_node.type == 'REROUTE':
                        extend_links_through_reroutes(links_to_draw, prev_node, 'backward', visited_nodes)

def trace_all_reroute_links(selected_node, links_to_draw, visited_nodes=None):
    """旧模式：仅收集选定节点的直接连线 + Reroute 延伸"""
    if visited_nodes is None:
        visited_nodes = set()
    if selected_node in visited_nodes:
        return
    visited_nodes.add(selected_node)
    
    for output in selected_node.outputs:
        if output.enabled:
            for link in output.links:
                links_to_draw.add(link)
                if link.to_node and link.to_node.type == 'REROUTE':
                    extend_links_through_reroutes(links_to_draw, link.to_node, 'forward')
    
    for input_socket in selected_node.inputs:
        if input_socket.enabled:
            for link in input_socket.links:
                links_to_draw.add(link)
                if link.from_node and link.from_node.type == 'REROUTE':
                    extend_links_through_reroutes(links_to_draw, link.from_node, 'backward')

# --- 新的逻辑：真正的深度递归遍历 ---
def traverse_recursive(current_node, direction, collected_links, visited_nodes):
    """
    深度优先搜索，遍历整个节点树的数据流
    
    direction: 'forward' (downstream) or 'backward' (upstream)
    collected_links: 收集到的连线集合
    visited_nodes: 已访问的节点集合（用于防止循环）
    """
    if current_node in visited_nodes:
        return
    visited_nodes.add(current_node)

    if direction == 'forward':
        # 向下查找：Output -> Links -> Next Node
        for output in current_node.outputs:
            if output.enabled:
                for link in output.links:
                    if link not in collected_links:
                        collected_links.add(link)
                        if link.to_node:
                            traverse_recursive(link.to_node, 'forward', collected_links, visited_nodes)
    
    elif direction == 'backward':
        # 向上查找：Input -> Links -> Previous Node
        for input_socket in current_node.inputs:
            if input_socket.enabled:
                for link in input_socket.links:
                    if link not in collected_links:
                        collected_links.add(link)
                        if link.from_node:
                            traverse_recursive(link.from_node, 'backward', collected_links, visited_nodes)

def draw_colorful_connections():
    context = bpy.context
    if context.space_data is None or context.space_data.type != 'NODE_EDITOR':
        return
    tree = context.space_data.node_tree
    if not tree:
        return
    
    settings = get_panel_settings()
    if not settings.get('enable_colorful_connections', True):
        return

    links_to_draw = set()
    nodes_to_outline = set()  # 用来画边框的节点

    trace_mode = settings.get('trace_mode', 'ALL_SELECTED')

    # --- 逻辑分支 ---
    if trace_mode == 'ALL_SELECTED':
        # 原有逻辑：所有选中节点都发光
        selected_nodes = context.selected_nodes
        if not selected_nodes:
            return
        nodes_to_outline = set(selected_nodes)  # 边框只画选中的
        for node in selected_nodes:
            trace_all_reroute_links(node, links_to_draw)
            
    elif trace_mode == 'ACTIVE_FLOW':
        # 新逻辑：仅追踪活动节点的数据流
        lock_flow = settings.get('lock_flow', False)
        
        # 如果取消了锁定，清除保存的状态
        if not lock_flow and _locked_flow_data['is_locked']:
            _locked_flow_data['is_locked'] = False
            _locked_flow_data['links'].clear()
            _locked_flow_data['nodes'].clear()
        
        # 检查是否需要使用固定的流
        if lock_flow and _locked_flow_data['is_locked']:
            # 使用固定的流数据
            links_to_draw.update(_locked_flow_data['links'])
            nodes_to_outline.update(_locked_flow_data['nodes'])
        else:
            # 重新计算流
            active_node = context.active_node
            if not active_node:
                return
            
            # 边框始终画活动节点
            nodes_to_outline.add(active_node)
            
            direction = settings.get('flow_direction', 'DOWNSTREAM')
            
            if direction == 'BOTH':
                # 双向模式：使用两个独立的 visited_nodes 集合，避免相互干扰
                visited_nodes_forward = set()
                visited_nodes_backward = set()
                
                # 向下遍历
                traverse_recursive(active_node, 'forward', links_to_draw, visited_nodes_forward)
                # 向上遍历
                traverse_recursive(active_node, 'backward', links_to_draw, visited_nodes_backward)
            else:
                # 单向模式：使用一个集合即可
                visited_nodes_trace = set()
                if direction == 'DOWNSTREAM':
                    traverse_recursive(active_node, 'forward', links_to_draw, visited_nodes_trace)
                elif direction == 'UPSTREAM':
                    traverse_recursive(active_node, 'backward', links_to_draw, visited_nodes_trace)
            
            # 如果启用了锁定，保存当前的流状态
            if lock_flow:
                _locked_flow_data['links'] = links_to_draw.copy()
                _locked_flow_data['nodes'] = nodes_to_outline.copy()
                _locked_flow_data['is_locked'] = True

    if not links_to_draw and not nodes_to_outline:
        return

    gpu.state.blend_set('ALPHA')

    v2d = context.region.view2d
    zoom = _view2d_zoom_factor(v2d)
    
    time_sec = time.time() * settings.get('animation_speed', 1.0)
    connection_color_type = settings.get('connection_color_type', 'RAINBOW')
    
    grad_cols = settings.get('gradient_colors', [])

    batch_lines_backing = []
    batch_lines_main = []
    batch_node_bbox = []

    batch_circles_backing = []
    batch_circles_start = []
    batch_circles_end = []

    # 处理节点边框
    if nodes_to_outline:
        border_thickness = settings.get('node_border_thickness', 3.0)
        bbox_width = max(1.0, border_thickness * zoom)
        pixel_radius = 4.0 * zoom
        
        for node in nodes_to_outline:
            bbox_poly = get_rounded_rect_path(
                node, 
                v2d, 
                radius=pixel_radius,
                thickness=bbox_width
            )
            if bbox_poly:
                batch_node_bbox.append(bbox_poly)

    socket_index_cache = {}
    curv_factor = get_curving_factor()

    for link in links_to_draw:
        fs = getattr(link, "from_socket", None)
        ts = getattr(link, "to_socket", None)
        if not fs or not ts:
            continue
        n1 = link.from_node
        n2 = link.to_node
        from_idx = _get_socket_index_cached(socket_index_cache, n1, fs, True)
        to_idx = _get_socket_index_cached(socket_index_cache, n2, ts, False)
        if from_idx is None or to_idx is None:
            continue
        # 计算位置
        try:
            # get_socket_loc 可能在特殊情况下失败，加个保护
            l1x, l1y = get_socket_loc(n1, True, from_idx)
            l2x, l2y = get_socket_loc(n2, False, to_idx)
        except:
            continue

        pts = get_native_link_points(link, v2d, curv_factor)
        if not pts or len(pts) < 2:
            continue
        batch_lines_backing.append(pts)
        batch_lines_main.append(pts)
        sx, sy = pts[0]
        tx, ty = pts[-1]
        batch_circles_backing.append((sx, sy))
        batch_circles_backing.append((tx, ty))
        batch_circles_start.append((sx, sy))
        batch_circles_end.append((tx, ty))

    width_backing = max(2.0, 9.0 * zoom)
    width_main = max(1.5, settings.get('line_thickness', 2.0) * zoom)

    # 1. Backing (Black outline)
    draw_batch_lines(batch_lines_backing, 'SMOOTH_COLOR', width_backing, colors=[(0, 0, 0, 0.55)])

    # 2. Main Lines
    if connection_color_type == 'RAINBOW':
        draw_batch_lines(batch_lines_main, 'RAINBOW', width_main, time_sec=time_sec)
    else:
        # 使用 GRADIENT 着色器，传入5个颜色
        draw_batch_lines(batch_lines_main, 'GRADIENT', width_main, colors=grad_cols, time_sec=time_sec)

    # 3. Circles
    draw_batch_circles(batch_circles_backing, 7.0 * zoom, (0, 0, 0, 0.55))

    if connection_color_type == 'RAINBOW':
        draw_batch_circles(batch_circles_start, 5.0 * zoom, _rainbow_rgba(0.0, time_sec))
        draw_batch_circles(batch_circles_end, 5.0 * zoom, _rainbow_rgba(1.0, time_sec))
    else:
        # 自定义模式下，端点颜色动态取色
        c_start = grad_cols[0] if grad_cols else (1,1,1,1)
        # 终点使用最后一个颜色
        c_end = grad_cols[-1] if grad_cols else c_start
        draw_batch_circles(batch_circles_start, 5.0 * zoom, c_start) 
        draw_batch_circles(batch_circles_end, 5.0 * zoom, c_end)

    # 4. Node Borders
    if batch_node_bbox:
        if connection_color_type == 'RAINBOW':
            draw_batch_lines(batch_node_bbox, 'RAINBOW', bbox_width, time_sec=time_sec)
        else:
            draw_batch_lines(batch_node_bbox, 'GRADIENT', bbox_width, colors=grad_cols, time_sec=time_sec)

    gpu.state.blend_set('NONE')
    if not bpy.app.timers.is_registered(force_redraw):
        bpy.app.timers.register(force_redraw, first_interval=0.01)

def force_redraw():
    try:
        for wm in bpy.data.window_managers:
            for window in wm.windows:
                for area in window.screen.areas:
                    if area.type == 'NODE_EDITOR':
                        area.tag_redraw()
    except:
        pass
    return None

def register():
    global draw_handler
    draw_handler = bpy.types.SpaceNodeEditor.draw_handler_add(
        draw_colorful_connections, (), 'WINDOW', 'POST_PIXEL'
    )

def unregister():
    global draw_handler
    if draw_handler:
        bpy.types.SpaceNodeEditor.draw_handler_remove(draw_handler, 'WINDOW')
        draw_handler = None