PROBLEM 1:
GENERATED WRONG JSON with ValueError: Location 'gallery_wall' not found in boundary_sets or point_nodes
FIX was: INSIDE THE BACKEND because the details were to small


PROBLEM 2:
The run now gives 22 → 235 °C at the wall. That looks about 4× too hot, and the arithmetic in your own _comment says why:
flux = 200 / ((pi/2) * 1.25) = 101.86 W/m2
That divides the full 200 W/m by the quarter arc. Your model is a quarter (sym_bottom + sym_left), so it should carry a quarter of the power over that arc — or equivalently the full power over the full circumference:
200 / (2*pi*1.25) = 25.46 W/m2        (exactly 101.86 / 4)